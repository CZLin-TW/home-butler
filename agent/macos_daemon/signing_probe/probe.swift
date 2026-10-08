// Build together with generated ProbeConfig. Only PUBLIC test data is supported.
import Foundation
import Security
import CryptoKit
import Darwin

@_silgen_name("SecTrustedApplicationCopyRequirement")
func copyTrustedRequirement(_ app: SecTrustedApplication, _ out: UnsafeMutablePointer<SecRequirement?>) -> OSStatus
@_silgen_name("SecKeychainGetKeychainVersion")
func keychainVersion(_ chain: SecKeychain, _ out: UnsafeMutablePointer<UInt32>) -> OSStatus

enum Failure: Error { case status(OSStatus), invalid }
func check(_ status: OSStatus) throws { if status != errSecSuccess { throw Failure.status(status) } }
let publicValue = Data("PUBLIC-NONSECRET-HOMEBUTLER-SIGNING-PROBE".utf8)
let publicPassword = "PUBLIC-THROWAWAY-KEYCHAIN-PASSWORD"
let ownPath = URL(fileURLWithPath: CommandLine.arguments[0]).standardizedFileURL.resolvingSymlinksInPath().path

func chainPath(_ chain: SecKeychain) throws -> String {
    var length: UInt32 = 4096
    var bytes = [CChar](repeating: 0, count: Int(length))
    try check(SecKeychainGetPath(chain, &length, &bytes))
    return String(cString: bytes)
}

func openChain(_ system: Bool) throws -> SecKeychain {
    let path = system ? "/Library/Keychains/System.keychain" : ProbeConfig.fixture
    var result: SecKeychain?
    try check(SecKeychainOpen(path, &result))
    guard let result, try chainPath(result) == path else { throw Failure.invalid }
    return result
}

func query(_ chain: SecKeychain) -> [String: Any] {
    [kSecClass as String: kSecClassGenericPassword,
     kSecAttrService as String: ProbeConfig.service,
     kSecAttrAccount as String: "public-fixture",
     kSecMatchSearchList as String: [chain],
     kSecUseAuthenticationUI as String: kSecUseAuthenticationUIFail]
}

func item(_ chain: SecKeychain) throws -> SecKeychainItem {
    var q = query(chain)
    q[kSecReturnRef as String] = true
    q[kSecMatchLimit as String] = kSecMatchLimitAll
    var result: CFTypeRef?
    try check(SecItemCopyMatching(q as CFDictionary, &result))
    guard let values = result as? [AnyObject], values.count == 1,
          CFGetTypeID(values[0]) == SecKeychainItemGetTypeID() else { throw Failure.invalid }
    let value = unsafeBitCast(values[0], to: SecKeychainItem.self)
    var actual: SecKeychain?
    try check(SecKeychainItemCopyKeychain(value, &actual))
    guard let actual, try chainPath(actual) == chainPath(chain) else { throw Failure.invalid }
    return value
}

func aclMetadata(_ chain: SecKeychain) throws -> [String: Any] {
    var access: SecAccess?
    try check(SecKeychainItemCopyAccess(try item(chain), &access))
    guard let access else { throw Failure.invalid }
    var list: CFArray?
    try check(SecAccessCopyACLList(access, &list))
    guard let values = list as? [AnyObject] else { throw Failure.invalid }
    var rows: [String] = []
    var partitions: [[String]] = []
    var decryptRequirements: [String] = []
    for object in values {
        guard CFGetTypeID(object) == SecACLGetTypeID() else { throw Failure.invalid }
        let acl = unsafeBitCast(object, to: SecACL.self)
        guard let auth = SecACLCopyAuthorizations(acl) as? [String] else { throw Failure.invalid }
        var apps: CFArray?
        var description: CFString?
        var prompt = SecKeychainPromptSelector(rawValue: 0)
        try check(SecACLCopyContents(acl, &apps, &description, &prompt))
        var entries: [[String: String]] = []
        for application in (apps as? [AnyObject] ?? []) {
            guard CFGetTypeID(application) == SecTrustedApplicationGetTypeID() else { throw Failure.invalid }
            let trusted = unsafeBitCast(application, to: SecTrustedApplication.self)
            var req: SecRequirement?
            var text: CFString?
            var data: CFData?
            try check(copyTrustedRequirement(trusted, &req))
            guard let req else { throw Failure.invalid }
            try check(SecRequirementCopyString(req, [], &text))
            try check(SecTrustedApplicationCopyData(trusted, &data))
            guard let text, let data else { throw Failure.invalid }
            entries.append(["requirement": text as String, "path": (data as Data).base64EncodedString()])
            if auth.contains(kSecACLAuthorizationDecrypt as String) { decryptRequirements.append(text as String) }
        }
        if auth.contains(kSecACLAuthorizationDecrypt as String) {
            guard let entries = apps as? [AnyObject], entries.count == 1 else { throw Failure.invalid }
        }
        guard !auth.contains(kSecACLAuthorizationAny as String) else { throw Failure.invalid }
        let desc = description as String? ?? ""
        if auth.contains(kSecACLAuthorizationPartitionID as String) {
            guard desc.count % 2 == 0 else { throw Failure.invalid }
            var data = Data()
            var i = desc.startIndex
            while i < desc.endIndex {
                let end = desc.index(i, offsetBy: 2)
                guard let byte = UInt8(desc[i..<end], radix: 16) else { throw Failure.invalid }
                data.append(byte); i = end
            }
            guard let p = try PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any],
                  let ids = p["Partitions"] as? [String], p.count == 1 else { throw Failure.invalid }
            partitions.append(ids)
        }
        let row: [String: Any] = ["auth": auth.sorted(), "apps": entries, "appsNull": apps == nil,
                                  "description": desc, "prompt": prompt.rawValue]
        rows.append(try JSONSerialization.data(withJSONObject: row, options: [.sortedKeys]).base64EncodedString())
    }
    guard decryptRequirements.count == 1 else { throw Failure.invalid }
    var version: UInt32 = 0
    try check(keychainVersion(chain, &version))
    var ownerUID: uid_t = 0; var ownerGID: gid_t = 0
    var ownerType: SecAccessOwnerType = 0; var ownerACL: CFArray?
    try check(SecAccessCopyOwnerAndACL(access, &ownerUID, &ownerGID, &ownerType, &ownerACL))
    let value: [String: Any] = ["rows": rows.sorted(), "owner": [ownerUID, ownerGID, ownerType]]
    let bytes = try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
    let hash = SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
    return ["acl_sha256": hash, "database_version": version, "partitions": partitions,
            "decrypt_requirement": decryptRequirements[0], "item_count": 1]
}

func read(_ chain: SecKeychain) -> (OSStatus, Bool) {
    var q = query(chain)
    q[kSecReturnData as String] = true
    q[kSecMatchLimit as String] = kSecMatchLimitOne
    var result: CFTypeRef?
    let status = SecItemCopyMatching(q as CFDictionary, &result)
    return (status, status == 0 && result as? Data == publicValue)
}

func create(_ chain: SecKeychain) throws {
    var trusted: SecTrustedApplication?
    var access: SecAccess?
    try check(SecTrustedApplicationCreateFromPath(ownPath, &trusted))
    guard let trusted else { throw Failure.invalid }
    try check(SecAccessCreate("HomeButler PUBLIC signing experiment" as CFString, [trusted] as CFArray, &access))
    guard let access else { throw Failure.invalid }
    let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                           kSecAttrService as String: ProbeConfig.service,
                           kSecAttrAccount as String: "public-fixture",
                           kSecAttrLabel as String: "HomeButler PUBLIC signing experiment",
                           kSecValueData as String: publicValue,
                           kSecAttrAccess as String: access,
                           kSecUseKeychain as String: chain]
    try check(SecItemAdd(q as CFDictionary, nil))
}

func createFixture() throws {
    guard !FileManager.default.fileExists(atPath: ProbeConfig.fixture) else { throw Failure.invalid }
    var result: SecKeychain?
    try publicPassword.withCString { password in
        try check(SecKeychainCreate(ProbeConfig.fixture, UInt32(publicPassword.utf8.count), password, false, nil, &result))
    }
    // Remove only this newly-created keychain from the search list; all queries are explicit.
    var list: CFArray?
    try check(SecKeychainCopySearchList(&list))
    guard let values = list as? [SecKeychain] else { throw Failure.invalid }
    let retained = try values.filter { try chainPath($0) != ProbeConfig.fixture }
    try check(SecKeychainSetSearchList(retained as CFArray))
}

func main() throws {
    try check(SecKeychainSetUserInteractionAllowed(false))
    guard CommandLine.arguments.count == 3 else { throw Failure.invalid }
    let command = CommandLine.arguments[1]
    let scope = CommandLine.arguments[2]
    guard ["fixture", "system"].contains(scope),
          ProbeConfig.service.hasPrefix("org.homebutler.telemetry.signing-probe.") else { throw Failure.invalid }
    if command == "create-fixture" {
        guard scope == "fixture" else { throw Failure.invalid }
        try createFixture()
        print("{\"created_fixture\":true}")
        return
    }
    let chain = try openChain(scope == "system")
    var output: [String: Any] = ["build": ProbeConfig.build, "uid": getuid(), "scope": scope]
    switch command {
    case "create":
        try create(chain)
        output["created"] = true
        output["metadata"] = try aclMetadata(chain)
    case "read":
        let (status, matched) = read(chain)
        output["status"] = status; output["matched"] = matched
    case "metadata": output["metadata"] = try aclMetadata(chain)
    case "delete":
        let (status, matched) = read(chain)
        try check(status)
        guard matched else { throw Failure.invalid }
        try check(SecKeychainItemDelete(try item(chain)))
        output["deleted"] = true
    default: throw Failure.invalid
    }
    let bytes = try JSONSerialization.data(withJSONObject: output, options: [.sortedKeys])
    print(String(decoding: bytes, as: UTF8.self))
}

do { try main() }
catch Failure.status(let status) { print("{\"error_status\":\(status)}"); exit(1) }
catch { print("{\"error\":\"probe_validation_failed\"}"); exit(1) }
