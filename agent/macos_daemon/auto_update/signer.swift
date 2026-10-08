// Local signing-key storage helper. Never accesses telemetry credentials.
import Foundation
import Security
import Darwin

enum Failure: Error { case invalid, status(OSStatus) }
func check(_ s: OSStatus) throws { if s != 0 { throw Failure.status(s) } }
func privateFile(_ path: String, directory: Bool = false) throws {
    var value=stat()
    guard lstat(path,&value)==0, value.st_uid==getuid(), value.st_mode & 0o077 == 0,
          value.st_mode & S_IFMT == (directory ? S_IFDIR : S_IFREG) else { throw Failure.invalid }
}
func main() throws {
#if !SIGNER_FIXTURE
    guard getuid()==0,geteuid()==0 else { throw Failure.invalid }
#endif
    SecKeychainSetUserInteractionAllowed(false)
    guard CommandLine.arguments.count==3 else { throw Failure.invalid }
    let mode=CommandLine.arguments[1]
    let root=URL(fileURLWithPath:CommandLine.arguments[2]).standardizedFileURL
    try privateFile(root.path,directory:true)
    let path=root.appendingPathComponent("signing.keychain-db").path
    let passwordPath=root.appendingPathComponent("unlock.data").path
    if mode=="create" {
        guard !FileManager.default.fileExists(atPath:path),!FileManager.default.fileExists(atPath:passwordPath) else { throw Failure.invalid }
        var password=Data(count:48)
        try password.withUnsafeMutableBytes { p in try check(SecRandomCopyBytes(kSecRandomDefault,p.count,p.baseAddress!)) }
        defer { password.resetBytes(in:0..<password.count) }
        let fd=open(passwordPath,O_CREAT|O_EXCL|O_WRONLY|O_NOFOLLOW,0o600)
        guard fd>=0 else { throw Failure.invalid }
        defer { close(fd) }
        guard password.withUnsafeBytes({write(fd,$0.baseAddress,$0.count)})==password.count,fsync(fd)==0 else { throw Failure.invalid }
        var chain:SecKeychain?
        try password.withUnsafeBytes { p in try check(SecKeychainCreate(path,UInt32(p.count),p.baseAddress,false,nil,&chain)) }
        guard let chain else { throw Failure.invalid }
        var list:CFArray?
        try check(SecKeychainCopySearchList(&list))
        guard let entries=list as? [SecKeychain] else { throw Failure.invalid }
        let retained=entries.filter { !CFEqual($0,chain) }
        try check(SecKeychainSetSearchList(retained as CFArray))
        guard chmod(path,0o600)==0 else { throw Failure.invalid }
        print("SIGNING_KEYCHAIN_CREATED")
        return
    }
    try privateFile(path);try privateFile(passwordPath)
    var password=try Data(contentsOf:URL(fileURLWithPath:passwordPath))
    guard password.count==48 else { throw Failure.invalid }
    defer {password.resetBytes(in:0..<password.count)}
    var chain:SecKeychain?
    try check(SecKeychainOpen(path,&chain))
    guard let chain else {throw Failure.invalid}
    if mode=="lock" {try check(SecKeychainLock(chain));print("SIGNER_LOCKED");return}
    try password.withUnsafeBytes { p in try check(SecKeychainUnlock(chain,UInt32(p.count),p.baseAddress,true)) }
    if mode=="unlock" {print("SIGNER_UNLOCKED");return}
    guard mode=="import" else {throw Failure.invalid}
    let p12=root.appendingPathComponent("identity.p12")
    try privateFile(p12.path)
    var data=try Data(contentsOf:p12)
    guard data.count<16384 else {throw Failure.invalid}
    defer {data.resetBytes(in:0..<data.count)}
    var app:SecTrustedApplication?;var access:SecAccess?
    try check(SecTrustedApplicationCreateFromPath("/usr/bin/codesign",&app))
    guard let app else {throw Failure.invalid}
    try check(SecAccessCreate("HomeButler local update signing" as CFString,[app] as CFArray,&access))
    guard let access else {throw Failure.invalid}
    var parameters=SecItemImportExportKeyParameters()
    parameters.version=UInt32(SEC_KEY_IMPORT_EXPORT_PARAMS_VERSION)
    // PKCS#12 is a transient root-only file, not a password-based security
    // boundary. A fixed transport label avoids empty-password interop failures.
    let empty="PUBLIC-LOCAL-P12-TRANSPORT" as CFString
    let usage=[kSecAttrCanSign] as CFArray
    let attributes=[kSecAttrIsPermanent,kSecAttrIsSensitive] as CFArray
    parameters.passphrase=Unmanaged.passUnretained(empty)
    parameters.accessRef=Unmanaged.passUnretained(access)
    parameters.keyUsage=Unmanaged.passUnretained(usage)
    parameters.keyAttributes=Unmanaged.passUnretained(attributes)
    var format=SecExternalFormat.formatPKCS12
    var type=SecExternalItemType.itemTypeAggregate
    var items:CFArray?
    try check(SecItemImport(data as CFData,nil,&format,&type,[],&parameters,chain,&items))
    print("LOCAL_SIGNING_IDENTITY_IMPORTED")
}
do {try main()}
catch Failure.status(let s) {print("Signing Keychain failed status=\(s)");exit(1)}
catch {print("Signing Keychain scope/permission validation failed");exit(1)}
