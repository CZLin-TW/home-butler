import Foundation
import Security
import Darwin

let label = Deployment.label
let service = Deployment.service
let account = Deployment.account
let origin = Deployment.origin
let endpoint = URL(string: origin + "/api/computers/heartbeat")!
let fm = FileManager.default
let home = URL(fileURLWithPath: Deployment.home)
let install = URL(fileURLWithPath: Deployment.install)
let executable = install.appendingPathComponent("bin/mini-telemetry")
let plist = URL(fileURLWithPath: "/Library/LaunchDaemons/\(label).plist")
let statusFile = install.appendingPathComponent("state/status.log")

enum Failure: Error { case invalidPayload, keychain(OSStatus), acknowledgement, process, wrongLocation, input, alreadyInstalled }

func privateDirectory(_ url: URL) throws {
    try fm.createDirectory(at: url, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
}

func note(_ message: String) {
    // Callers supply only fixed status strings / numeric OSStatus, never arbitrary errors or data.
    let line = "\(ISO8601DateFormatter().string(from: Date())) \(message)\n"
    do {
        if let size = try? fm.attributesOfItem(atPath: statusFile.path)[.size] as? NSNumber,
           size.intValue > 262144 {
            let old = install.appendingPathComponent("state/status.log.1")
            if fm.fileExists(atPath: old.path) { try fm.removeItem(at: old) }
            try fm.moveItem(at: statusFile, to: old)
        }
        if !fm.fileExists(atPath: statusFile.path) {
            fm.createFile(atPath: statusFile.path, contents: nil, attributes: [.posixPermissions: 0o600])
        }
        let handle = try FileHandle(forWritingTo: statusFile)
        defer { try? handle.close() }
        try handle.seekToEnd()
        try handle.write(contentsOf: Data(line.utf8))
    } catch { /* never expose raw errors */ }
}

func child(_ path: String, _ args: [String], timeout: TimeInterval = 15, capture: Bool = false) throws -> Data {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: path)
    p.arguments = args
    // No inherited key, proxy, Python or shell environment. Keychain secrets never enter child processes.
    p.environment = ["PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": home.path, "LANG": "en_US.UTF-8"]
    let pipe = Pipe()
    p.standardOutput = capture ? pipe : FileHandle.nullDevice
    p.standardError = FileHandle.nullDevice
    p.standardInput = FileHandle.nullDevice
    try p.run()
    let deadline = Date().addingTimeInterval(timeout)
    while p.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.05) }
    if p.isRunning {
        p.terminate()
        Thread.sleep(forTimeInterval: 0.1)
        if p.isRunning { kill(p.processIdentifier, SIGKILL) }
        p.waitUntilExit()
        throw Failure.process
    }
    guard p.terminationStatus == 0 else { throw Failure.process }
    return capture ? pipe.fileHandleForReading.readDataToEndOfFile() : Data()
}

func payload(from data: Data) throws -> Data {
    guard data.count < 16384,
          let outer = try JSONSerialization.jsonObject(with: data) as? [String: Any],
          let raw = outer["heartbeat"] as? [String: Any],
          raw["ip"] as? String == account, raw["hostname"] as? String == Deployment.hostname else { throw Failure.invalidPayload }
    let keys = Set(["ip", "hostname", "cpu_model", "gpu_model", "cpu_pct", "ram_pct", "gpu_pct", "cpu_temp_c", "gpu_temp_c", "fah", "smc_temperature"])
    guard Set(raw.keys) == keys else { throw Failure.invalidPayload }
    for name in ["cpu_model", "gpu_model"] {
        guard let value = raw[name] as? String, value.count <= 128 else { throw Failure.invalidPayload }
    }
    for name in ["cpu_pct", "ram_pct", "gpu_pct"] {
        if name == "gpu_pct", raw[name] is NSNull { continue }
        guard let value = raw[name] as? NSNumber, CFGetTypeID(value) != CFBooleanGetTypeID(),
              value.doubleValue.isFinite, (0...100).contains(value.doubleValue) else { throw Failure.invalidPayload }
    }
    for name in ["cpu_temp_c", "gpu_temp_c", "fah"] {
        guard raw[name] is NSNull else { throw Failure.invalidPayload }
    }
    guard let smc = raw["smc_temperature"] as? [String: Any],
          Set(smc.keys) == Set(["tcmb_c", "tcmz_c"]) else { throw Failure.invalidPayload }
    for name in ["tcmb_c", "tcmz_c"] {
        if smc[name] is NSNull { continue }
        guard let value = smc[name] as? NSNumber, CFGetTypeID(value) != CFBooleanGetTypeID(),
              value.doubleValue.isFinite, value.doubleValue > 0, value.doubleValue <= 150 else { throw Failure.invalidPayload }
    }
    return try JSONSerialization.data(withJSONObject: raw)
}

func collect() throws -> Data {
    let raw = try child(install.appendingPathComponent("runtime/" + Deployment.python).path,
        ["-I", install.appendingPathComponent("collector/macos_metrics.py").path, "--ip", account, "--hostname", Deployment.hostname], capture: true)
    return try payload(from: raw)
}

func systemKeychain() throws -> SecKeychain {
    var keychain: SecKeychain?
    let status = SecKeychainOpen("/Library/Keychains/System.keychain", &keychain)
    guard status == errSecSuccess, let keychain else { throw Failure.keychain(status) }
    return keychain
}

func baseQuery(_ itemService: String = service) throws -> [String: Any] {
    return [kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: itemService, kSecAttrAccount as String: account,
            kSecMatchSearchList as String: [try systemKeychain()]]
}

func readKey(_ itemService: String = service) throws -> Data {
    // GUI interaction disabled for this process; a locked/changed ACL fails closed.
    SecKeychainSetUserInteractionAllowed(false)
    var q = try baseQuery(itemService)
    q[kSecReturnData as String] = true
    q[kSecMatchLimit as String] = kSecMatchLimitOne
    q[kSecUseAuthenticationUI as String] = kSecUseAuthenticationUIFail
    var result: CFTypeRef?
    let status = SecItemCopyMatching(q as CFDictionary, &result)
    guard status == errSecSuccess, let data = result as? Data, !data.isEmpty else { throw Failure.keychain(status) }
    return data
}

func saveKey(_ key: Data, _ itemService: String = service) throws {
    SecKeychainSetUserInteractionAllowed(false)
    var trusted: SecTrustedApplication?
    var access: SecAccess?
    var status = SecTrustedApplicationCreateFromPath(executable.path, &trusted)
    guard status == errSecSuccess, let trusted else { throw Failure.keychain(status) }
    status = SecAccessCreate("HomeButler Mini Telemetry" as CFString, [trusted] as CFArray, &access)
    guard status == errSecSuccess, let access else { throw Failure.keychain(status) }
    // Explicit System keychain and exact executable ACL; no broad -A access.
    let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: itemService, kSecAttrAccount as String: account,
        kSecAttrLabel as String: "HomeButler Mini Telemetry",
        kSecAttrAccess as String: access, kSecUseKeychain as String: try systemKeychain(),
        kSecValueData as String: key]
    status = SecItemAdd(q as CFDictionary, nil)
    // Never overwrite or silently change an existing item's ACL.
    guard status == errSecSuccess else { throw Failure.keychain(status) }
}

final class NoRedirect: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}

func request(body: Data, key: Data) throws -> URLRequest {
    guard let value = String(data: key, encoding: .utf8), !value.isEmpty,
          value == value.trimmingCharacters(in: .whitespacesAndNewlines),
          !value.contains("\r"), !value.contains("\n") else { throw Failure.input }
    var r = URLRequest(url: endpoint)
    r.httpMethod = "POST"
    r.httpBody = body
    r.timeoutInterval = 15
    r.setValue("application/json", forHTTPHeaderField: "Content-Type")
    r.setValue(value, forHTTPHeaderField: "X-API-Key")
    return r
}

func acknowledged(_ code: Int, _ data: Data) -> Bool {
    guard code == 200, data.count < 4096,
          let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any], object.count == 1,
          let ok = object["ok"] as? NSNumber, CFGetTypeID(ok) == CFBooleanGetTypeID() else { return false }
    return ok.boolValue
}

func transmit(_ body: Data, _ key: Data) throws {
    let config = URLSessionConfiguration.ephemeral
    config.connectionProxyDictionary = [:]
    config.httpCookieStorage = nil
    config.urlCredentialStorage = nil
    config.urlCache = nil
    config.timeoutIntervalForRequest = 15
    config.timeoutIntervalForResource = 20
    let session = URLSession(configuration: config, delegate: NoRedirect(), delegateQueue: nil)
    defer { session.invalidateAndCancel() }
    let done = DispatchSemaphore(value: 0)
    var accepted = false
    let task = session.dataTask(with: try request(body: body, key: key)) { data, response, error in
        if error == nil, let response = response as? HTTPURLResponse, let data {
            accepted = acknowledged(response.statusCode, data)
        }
        done.signal()
    }
    task.resume()
    guard done.wait(timeout: .now() + 22) == .success else { task.cancel(); throw Failure.acknowledgement }
    guard accepted else { throw Failure.acknowledgement }
}

func heartbeat(sample: () throws -> Data, credential: () throws -> Data,
               send: (Data, Data) throws -> Void) throws {
    let body = try sample() // No secret exists while executing the Python collector.
    var key = try credential()
    defer { key.resetBytes(in: 0..<key.count) }
    try send(body, key)
}

func manifest() -> [String: Any] {
    ["Label": label, "ProgramArguments": [executable.path, "run"], "RunAtLoad": true,
     "StartInterval": 60, "ProcessType": "Background", "WorkingDirectory": install.path,
     "UserName": Deployment.user, "GroupName": Deployment.group, "StandardOutPath": "/dev/null", "StandardErrorPath": "/dev/null", "Umask": 0o077]
}

func installedLocation() throws {
    let actual = URL(fileURLWithPath: CommandLine.arguments[0]).standardizedFileURL.resolvingSymlinksInPath()
    guard actual == executable.standardizedFileURL.resolvingSymlinksInPath() else { throw Failure.wrongLocation }
}

// Scope is fixed. No access to the former login Keychain item.
let probeService = service + ".probe"
let probeValue = Data("PUBLIC-NONSECRET-BOOT-PROBE-V1".utf8)
@_silgen_name("SecTrustedApplicationCopyRequirement")
func copyTrustedRequirement(_ app: SecTrustedApplication, _ requirement: UnsafeMutablePointer<SecRequirement?>) -> OSStatus
func requireRoot() throws {
    guard getuid() == 0, geteuid() == 0 else { throw Failure.process }
    try installedLocation()
}
@_silgen_name("SecKeychainGetKeychainVersion")
func keychainVersion(_ chain: SecKeychain, _ version: UnsafeMutablePointer<UInt32>) -> OSStatus

// Pure policy: a value rewrite may reset a modern keychain partition to its caller.
// Never repair that automatically or accept a different helper as the trusted sender.
func partitionValid(version: UInt32, entries: [[String]], expected: String) -> Bool {
    switch version {
    case 0x100, 0x101: return entries.isEmpty
    case 0x200: return entries == [[expected]]
    default: return false
    }
}

@discardableResult
func verifyAccess(_ itemService: String) throws -> [String: Any] {
    SecKeychainSetUserInteractionAllowed(false)
    var q = try baseQuery(itemService)
    q[kSecReturnRef as String] = true; q[kSecMatchLimit as String] = kSecMatchLimitAll
    var result: CFTypeRef?
    guard SecItemCopyMatching(q as CFDictionary, &result) == 0,
          let items = result as? [AnyObject], items.count == 1,
          CFGetTypeID(items[0]) == SecKeychainItemGetTypeID() else { throw Failure.invalidPayload }
    let item = unsafeBitCast(items[0], to: SecKeychainItem.self)
    var actual: SecKeychain?; var version: UInt32 = 0
    var length: UInt32 = 2048; var path = [CChar](repeating: 0, count: 2048)
    guard SecKeychainItemCopyKeychain(item, &actual) == 0, let actual,
          SecKeychainGetPath(actual, &length, &path) == 0,
          String(cString: path) == "/Library/Keychains/System.keychain",
          keychainVersion(actual, &version) == 0 else { throw Failure.invalidPayload }
    var access: SecAccess?; var aclList: CFArray?
    guard SecKeychainItemCopyAccess(item, &access) == 0, let access,
          SecAccessCopyACLList(access, &aclList) == 0,
          let acls = aclList as? [AnyObject] else { throw Failure.invalidPayload }
    var code: SecStaticCode?; var info: CFDictionary?; var ownReq: SecRequirement?; var ownText: CFString?
    guard SecStaticCodeCreateWithPath(executable as CFURL, [], &code) == 0, let code,
          SecStaticCodeCheckValidity(code, [], nil) == 0,
          SecCodeCopySigningInformation(code, SecCSFlags(rawValue:kSecCSSigningInformation), &info) == 0,
          let hash = (info as? [String:Any])?[kSecCodeInfoUnique as String] as? Data,
          SecCodeCopyDesignatedRequirement(code, [], &ownReq) == 0, let ownReq,
          SecRequirementCopyString(ownReq, [], &ownText) == 0, let ownText else { throw Failure.invalidPayload }
    let partition = "cdhash:" + hash.map { String(format:"%02x",$0) }.joined()
    var ownerUID: uid_t = 0; var ownerGID: gid_t = 0
    var ownerType: SecAccessOwnerType = 0; var ownerACL: CFArray?
    guard SecAccessCopyOwnerAndACL(access, &ownerUID, &ownerGID, &ownerType, &ownerACL) == 0 else { throw Failure.invalidPayload }
    var decrypts=0; var partitions:[[String]]=[]; var metadata:[String]=[]
    for object in acls {
        guard CFGetTypeID(object) == SecACLGetTypeID() else { throw Failure.invalidPayload }
        let acl=unsafeBitCast(object,to:SecACL.self)
        guard let auth=SecACLCopyAuthorizations(acl) as? [String], !auth.contains(kSecACLAuthorizationAny as String) else { throw Failure.invalidPayload }
        var apps:CFArray?;var desc:CFString?;var prompt=SecKeychainPromptSelector(rawValue:0)
        guard SecACLCopyContents(acl,&apps,&desc,&prompt)==0 else { throw Failure.invalidPayload }
        var appMetadata:[[String:String]]=[]
        for application in (apps as? [AnyObject] ?? []) {
            guard CFGetTypeID(application)==SecTrustedApplicationGetTypeID() else { throw Failure.invalidPayload }
            let trusted=unsafeBitCast(application,to:SecTrustedApplication.self)
            var data:CFData?;var requirement:SecRequirement?;var text:CFString?
            guard SecTrustedApplicationCopyData(trusted,&data)==0,let data,
                  copyTrustedRequirement(trusted,&requirement)==0,let requirement,
                  SecRequirementCopyString(requirement,[],&text)==0,let text else { throw Failure.invalidPayload }
            appMetadata.append(["path":(data as Data).base64EncodedString(),"requirement":text as String])
        }
        let metadataEntry:[String:Any]=["auth":auth.sorted(),"apps":appMetadata,"appsNull":apps==nil,"description":desc as String? ?? "","prompt":prompt.rawValue]
        metadata.append(try JSONSerialization.data(withJSONObject:metadataEntry,options:[.sortedKeys]).base64EncodedString())
        if auth.contains(kSecACLAuthorizationDecrypt as String) {
            decrypts+=1
            guard let applications=apps as? [AnyObject], applications.count==1 else { throw Failure.invalidPayload }
            let trusted=unsafeBitCast(applications[0],to:SecTrustedApplication.self)
            var req:SecRequirement?;var text:CFString?;var path:CFData?
            guard copyTrustedRequirement(trusted,&req)==0,let req,
                  SecRequirementCopyString(req,[],&text)==0,let text, text as String == ownText as String,
                  SecStaticCodeCheckValidity(code,[],req)==0,
                  SecTrustedApplicationCopyData(trusted,&path)==0,let path,
                  path as Data == Data((executable.path+"\0").utf8) else { throw Failure.invalidPayload }
        }
        if auth.contains(kSecACLAuthorizationPartitionID as String) {
            guard auth.count==1,let hex=desc as String?,hex.count%2==0 else { throw Failure.invalidPayload }
            var bytes=Data();var i=hex.startIndex
            while i<hex.endIndex {
                let n=hex.index(i,offsetBy:2)
                guard let b=UInt8(hex[i..<n],radix:16) else { throw Failure.invalidPayload }
                bytes.append(b);i=n
            }
            guard let p=try PropertyListSerialization.propertyList(from:bytes,format:nil) as? [String:Any],
                  p.count==1,let ids=p["Partitions"] as? [String] else { throw Failure.invalidPayload }
            partitions.append(ids)
        }
    }
    guard decrypts==1,partitionValid(version:version,entries:partitions,expected:partition) else { throw Failure.invalidPayload }
    return ["count":1,"keychain_path":String(cString:path),"database_version":version,
            "sender_cdhash":String(partition.dropFirst(7)),"owner":[ownerUID,ownerGID,ownerType],
            "acl":metadata.sorted(),"partitions":partitions]

}
func probeCreate() throws {
    try requireRoot()
    try saveKey(probeValue,probeService) // SecItemAdd refuses an existing item.
    try Data("created-by-this-install".utf8).write(to:install.appendingPathComponent("probe-owned"),options:.atomic)
    try verifyAccess(probeService)
    print("PROBE_CREATED: exact sender and format-aware ACL verified; no secret involved.")
}
func probe() throws {
    guard getuid()==Deployment.uid,geteuid()==Deployment.uid else { throw Failure.process }
    try installedLocation()
    _ = try collect() // Validates real metrics/runtime in system launchd context; no network.
    try verifyAccess(probeService)
    guard try readKey(probeService)==probeValue else { throw Failure.invalidPayload }
    note("probe passed expected_uid format_aware_acl no_network")
}
func probeDelete() throws {
    try requireRoot()
    guard try Data(contentsOf:install.appendingPathComponent("probe-owned")) == Data("created-by-this-install".utf8) else { throw Failure.process }
    let result=SecItemDelete(try baseQuery(probeService) as CFDictionary)
    guard result==0 else { throw Failure.keychain(result) }
    try fm.removeItem(at:install.appendingPathComponent("probe-owned"))
    print("PROBE_DELETED: temporary non-secret item only.")
}
func setup() throws {
    try requireRoot()
    guard isatty(STDIN_FILENO)==1,isatty(STDOUT_FILENO)==1 else { throw Failure.input }
    guard !fm.fileExists(atPath:install.appendingPathComponent("key-ready").path) else { throw Failure.alreadyInstalled }
    var q=try baseQuery();q[kSecReturnAttributes as String]=true
    var found:CFTypeRef?
    let exists=SecItemCopyMatching(q as CFDictionary,&found)
    guard exists==errSecItemNotFound else { throw Failure.alreadyInstalled }
    // The collector always runs as the configured non-root UID, even during administrator setup.
    let body=try child("/usr/bin/sudo",["-n","-u",Deployment.user,executable.path,"sample"],capture:true)
    print("System Keychain 新 item；目的地 \(origin)/api/computers/heartbeat，IP \(account)。")
    print("原 login Keychain item 不讀不改。只由本 sender 建立並限定其完整 cdhash。")
    var buffer=[CChar](repeating:0,count:4096)
    defer {for i in buffer.indices {buffer[i]=0}}
    let readOK=buffer.withUnsafeMutableBufferPointer { readpassphrase("API key（隱藏輸入）：",$0.baseAddress!,$0.count,RPP_REQUIRE_TTY) != nil }
    guard readOK else { throw Failure.input }
    var key=Data(buffer.prefix(while:{$0 != 0}).map{UInt8(bitPattern:$0)})
    for i in buffer.indices {buffer[i]=0}
    defer {key.resetBytes(in:0..<key.count)}
    _=try request(body:body,key:key)
    print("輸入 SEND：向上述既有目的地送一次目前 metrics，驗證 key；其他輸入取消。")
    guard readLine()=="SEND" else {throw Failure.input}
    try transmit(body,key)
    print("HTTP acknowledgement valid。輸入 CREATE 才新增 System Keychain item；其他輸入取消。")
    guard readLine()=="CREATE" else {throw Failure.input}
    try saveKey(key)
    try verifyAccess(service)
    try Data("ready".utf8).write(to:install.appendingPathComponent("key-ready"),options:.atomic)
    print("SYSTEM_KEY_READY: exact sender and format-aware ACL verified.")
}
func sample() throws {
    guard getuid()==Deployment.uid,geteuid()==Deployment.uid else {throw Failure.process}
    try installedLocation()
    FileHandle.standardOutput.write(try collect())
}


func run() throws {
    guard getuid() == Deployment.uid, geteuid() == Deployment.uid else { throw Failure.process }
    try installedLocation()
    let lock = open(install.appendingPathComponent("state/run.lock").path, O_CREAT | O_RDWR, 0o600)
    guard lock >= 0 else { throw Failure.process }
    defer { close(lock) }
    guard flock(lock, LOCK_EX | LOCK_NB) == 0 else { return }
    do {
        try verifyAccess(service)
        try heartbeat(sample: collect, credential: { try readKey() }, send: transmit)
        note("heartbeat acknowledged")
    } catch Failure.keychain(let code) {
        note("keychain unavailable status=\(code); no heartbeat sent")
        throw Failure.keychain(code)
    } catch {
        note("sample/send not confirmed; no immediate retry")
        throw Failure.process
    }
}

#if TESTING
func selfTests() throws {
    assert(partitionValid(version:0x100,entries:[],expected:"cdhash:sender"))
    assert(partitionValid(version:0x101,entries:[],expected:"cdhash:sender"))
    assert(!partitionValid(version:0x100,entries:[["cdhash:sender"]],expected:"cdhash:sender"))
    assert(partitionValid(version:0x200,entries:[["cdhash:sender"]],expected:"cdhash:sender"))
    for entries:[[String]] in [[],[["cdhash:repair-helper"]],[["apple:"]],[["cdhash:sender","apple:"]],[["cdhash:sender"],["cdhash:sender"]]] {
        assert(!partitionValid(version:0x200,entries:entries,expected:"cdhash:sender"))
    }
    assert(!partitionValid(version:0x300,entries:[],expected:"cdhash:sender"))
    let good = Data("""
    {"heartbeat":{"ip":"192.0.2.20","hostname":"Mac mini","cpu_model":"Example CPU","gpu_model":"Example GPU","cpu_pct":0,"ram_pct":50,"gpu_pct":null,"cpu_temp_c":null,"gpu_temp_c":null,"fah":null,"smc_temperature":{"tcmb_c":44.5,"tcmz_c":null}},"local_only":{"load_average_1_5_15":[1,2,3]}}
    """.utf8)
    let parsed = try payload(from: good)
    assert(!String(data: parsed, encoding: .utf8)!.contains("local_only"))
    var events: [String] = []
    try heartbeat(sample: { events.append("sample"); return parsed }, credential: { events.append("key"); return Data("FAKE".utf8) }, send: { body, key in events.append("send"); assert(body == parsed); assert(key == Data("FAKE".utf8)) })
    assert(events == ["sample", "key", "send"])
    events = []
    do {
        try heartbeat(sample: { throw Failure.process }, credential: { events.append("key"); return Data() }, send: { _,_ in events.append("send") })
        fatalError("expected failure")
    } catch { assert(events.isEmpty) }
    do {
        try heartbeat(sample: { parsed }, credential: { throw Failure.keychain(-1) }, send: { _,_ in events.append("send") })
        fatalError("expected failure")
    } catch { assert(events.isEmpty) }
    let req = try request(body: parsed, key: Data("FAKE".utf8))
    assert(req.url == endpoint && req.httpMethod == "POST" && req.value(forHTTPHeaderField: "X-API-Key") == "FAKE")
    assert(acknowledged(200, Data("{\"ok\":true}".utf8)))
    for (code, value) in [(302,"{\"ok\":true}"),(500,"{\"ok\":true}"),(200,"{\"ok\":1}"),(200,"{\"ok\":false}")] { assert(!acknowledged(code, Data(value.utf8))) }
    for text in ["FAKE-ascii-0123", "FAKE-UTF8-測試-κ"] {
        var buffer=text.utf8.map{CChar(bitPattern:$0)}+[CChar(0)]
        var key=Data(buffer.prefix(while:{$0 != 0}).map{UInt8(bitPattern:$0)})
        for i in buffer.indices {buffer[i]=0}
        let persisted=Data(key.map{$0}) // fake persistence, no Security calls
        key.resetBytes(in:0..<key.count)
        let returned:CFTypeRef=persisted as CFData
        let roundtrip=try request(body:parsed,key:returned as! Data)
        assert(roundtrip.value(forHTTPHeaderField:"X-API-Key")==text)
    }
    for bad in [" FAKE", "FAKE ", "FAKE\n", "FAKE\r", ""] {
        do { _=try request(body:parsed,key:Data(bad.utf8));fatalError("invalid key text accepted") } catch Failure.input {}
    }
    let config = manifest()
    assert(config["StartInterval"] as? Int == 60 && config["RunAtLoad"] as? Bool == true)
    assert(config["EnvironmentVariables"] == nil && config["KeepAlive"] == nil)
    assert(config["UserName"] as? String == Deployment.user && config["GroupName"] as? String == Deployment.group)
    assert(!service.isEmpty)
    assert(config["ProgramArguments"] as? [String] == [executable.path, "run"])
    for bad in [String(data: good, encoding: .utf8)!.replacingOccurrences(of: "\"cpu_pct\":0", with: "\"cpu_pct\":true"), String(data: good, encoding: .utf8)!.replacingOccurrences(of: "192.0.2.20", with: "192.0.2.1")] {
        do { _ = try payload(from: Data(bad.utf8)); fatalError("expected payload rejection") } catch {}
    }
    let goodText = String(data: good, encoding: .utf8)!
    for invalid in ["true", "0", "-1", "151", "\"44\"", "1e999"] {
        let bad = goodText.replacingOccurrences(of: "\"tcmb_c\":44.5", with: "\"tcmb_c\":\(invalid)")
        do { _ = try payload(from: Data(bad.utf8)); fatalError("invalid SMC accepted") } catch {}
    }
    for bad in [goodText.replacingOccurrences(of: "\"tcmz_c\":null", with: "\"unknown\":null"),
                goodText.replacingOccurrences(of: "\"cpu_temp_c\":null", with: "\"cpu_temp_c\":44.5")] {
        do { _ = try payload(from: Data(bad.utf8)); fatalError("invalid schema accepted") } catch {}
    }
    _ = try payload(from: Data(goodText.replacingOccurrences(of: "\"tcmb_c\":44.5", with: "\"tcmb_c\":null").utf8))
    var redirectDenied = false
    NoRedirect().urlSession(URLSession(configuration: .ephemeral), task: URLSession.shared.dataTask(with: endpoint), willPerformHTTPRedirection: HTTPURLResponse(url: endpoint, statusCode: 302, httpVersion: nil, headerFields: nil)!, newRequest: URLRequest(url: URL(string:"https://example.invalid")!)) { redirectDenied = $0 == nil }
    assert(redirectDenied)
    print("PASS: fake sampling/key/send order, failures, exact destination/schema, ack, redirect denial, non-root UID LaunchDaemon contract. No Keychain/network access.")
}
try selfTests()
#else
umask(0o077)
do {
    switch CommandLine.arguments.dropFirst().first {
    case "setup": try setup()
    case "sample": try sample()
    case "probe-create": try probeCreate()
    case "probe": try probe()
    case "probe-delete": try probeDelete()
    case "verify-access": try installedLocation(); try verifyAccess(service); print("ACCESS_VERIFIED")
    case "metadata":
        try installedLocation()
        FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject:verifyAccess(service),options:[.sortedKeys]))

    case "run": try run()
    default: print("Unsupported mode."); exit(2)
    }
} catch Failure.keychain(let code) {
    if CommandLine.arguments.last == "probe" { note("probe failed keychain status=\(code)") }
    if CommandLine.arguments.last != "run" { print("Keychain 操作未完成，status=\(code)。不會自動替換既有 key 或放寬權限。") }
    exit(1)
} catch {
    if CommandLine.arguments.last == "probe" { note("probe failed validation") }
    if CommandLine.arguments.last != "run" { print("操作未完成／已取消。沒有輸出秘密；請核對狀態。") }
    exit(1)
}
#endif
