// Compiled only with AUTO_UPDATE and locally generated, non-secret UpdateTrust.
// The old ad-hoc deployment and its existing validator remain unchanged.

func stablePartitionValid(version: UInt32, entries: [[String]]) -> Bool {
    // Real A/B/A and negative-control evidence exists only for database 0x100.
    // It may contain an inert creator-cdhash partition record. Never accept a
    // modern/unknown database on the strength of that legacy experiment.
    guard version == 0x100 else { return false }
    if entries.isEmpty { return true }
    guard entries.count == 1, entries[0].count == 1 else { return false }
    return entries[0][0].range(of:"^cdhash:[0-9a-f]{40}$",options:.regularExpression) != nil
}

func requirementText(_ app: SecTrustedApplication) throws -> String {
    var requirement: SecRequirement?; var text: CFString?
    guard copyTrustedRequirement(app,&requirement)==0,let requirement,
          SecRequirementCopyString(requirement,[],&text)==0,let text else { throw Failure.invalidPayload }
    return text as String
}

func checkedApplication(_ path: String, _ expected: String) throws -> SecTrustedApplication {
    var code: SecStaticCode?; var requirement: SecRequirement?; var app: SecTrustedApplication?
    guard SecRequirementCreateWithString(expected as CFString,[],&requirement)==0,let requirement,
          SecStaticCodeCreateWithPath(URL(fileURLWithPath:path) as CFURL,[],&code)==0,let code,
          SecStaticCodeCheckValidity(code,[],requirement)==0,
          SecTrustedApplicationCreateFromPath(path,&app)==0,let app,
          try requirementText(app)==expected else { throw Failure.invalidPayload }
    return app
}

func migrateAccess(rollback: Bool) throws {
    // Administrator-only, exact-item ACL transition. No kSecReturnData,
    // SecItemUpdate, key export, partition change, password or credential IPC.
    try requireRoot()
    try installedLocation()
    SecKeychainSetUserInteractionAllowed(false)
    var q = try baseQuery()
    q[kSecReturnRef as String]=true; q[kSecMatchLimit as String]=kSecMatchLimitAll
    var raw: CFTypeRef?
    guard SecItemCopyMatching(q as CFDictionary,&raw)==0,
          let items=raw as? [AnyObject],items.count==1,
          CFGetTypeID(items[0])==SecKeychainItemGetTypeID() else { throw Failure.invalidPayload }
    let item=unsafeBitCast(items[0],to:SecKeychainItem.self)
    var actual: SecKeychain?; var version: UInt32=0
    var length: UInt32=2048;var bytes=[CChar](repeating:0,count:2048)
    guard SecKeychainItemCopyKeychain(item,&actual)==0,let actual,
          SecKeychainGetPath(actual,&length,&bytes)==0,
          String(cString:bytes)=="/Library/Keychains/System.keychain",
          keychainVersion(actual,&version)==0,version==0x100 else { throw Failure.invalidPayload }
    let oldRequirement="cdhash H\""+UpdateTrust.originalCDHash+"\""
    let original=try checkedApplication(UpdateTrust.originalSender,oldRequirement)
    let updated=try checkedApplication(executable.path,UpdateTrust.requirement)
    let expectedFrom=rollback ? UpdateTrust.requirement : oldRequirement
    let expectedTo=rollback ? oldRequirement : UpdateTrust.requirement
    var access:SecAccess?;var list:CFArray?
    guard SecKeychainItemCopyAccess(item,&access)==0,let access,
          SecAccessCopyACLList(access,&list)==0,let acls=list as? [AnyObject] else { throw Failure.invalidPayload }
    var ownerUID:uid_t=0;var ownerGID:gid_t=0;var ownerType:SecAccessOwnerType=0;var ownerACL:CFArray?
    guard SecAccessCopyOwnerAndACL(access,&ownerUID,&ownerGID,&ownerType,&ownerACL)==0,
          ownerUID==0 else { throw Failure.invalidPayload }
    var decrypt:SecACL?;var oldDescription:CFString?;var oldPrompt=SecKeychainPromptSelector(rawValue:0)
    var currentRequirement=""
    for object in acls {
        guard CFGetTypeID(object)==SecACLGetTypeID() else { throw Failure.invalidPayload }
        let acl=unsafeBitCast(object,to:SecACL.self)
        guard let auth=SecACLCopyAuthorizations(acl) as? [String],
              !auth.contains(kSecACLAuthorizationAny as String) else { throw Failure.invalidPayload }
        if auth.contains(kSecACLAuthorizationDecrypt as String) {
            guard decrypt==nil else { throw Failure.invalidPayload }
            var apps:CFArray?
            guard SecACLCopyContents(acl,&apps,&oldDescription,&oldPrompt)==0,
                  let applications=apps as? [AnyObject],applications.count==1,
                  CFGetTypeID(applications[0])==SecTrustedApplicationGetTypeID() else { throw Failure.invalidPayload }
            currentRequirement=try requirementText(unsafeBitCast(applications[0],to:SecTrustedApplication.self))
            decrypt=acl
        }
    }
    guard let decrypt,let oldDescription else { throw Failure.invalidPayload }
    if currentRequirement==expectedTo { print("ACCESS_ALREADY_SELECTED");return }
    guard currentRequirement==expectedFrom else { throw Failure.invalidPayload }
    let replacement=rollback ? original : updated
    guard SecACLSetContents(decrypt,[replacement] as CFArray,oldDescription,oldPrompt)==0 else { throw Failure.invalidPayload }
    let status=SecKeychainItemSetAccess(item,access)
    guard status==0 else { throw Failure.keychain(status) }
    if !rollback { _=try verifyAccess(service) }
    print(rollback ? "ORIGINAL_ACCESS_RESTORED" : "STABLE_ACCESS_SELECTED")
}

func publicAccessTest() throws {
    guard service.hasPrefix("org.homebutler.telemetry.update-probe."),
          getuid()==Deployment.uid,geteuid()==Deployment.uid else { throw Failure.process }
    try installedLocation()
    _=try verifyAccess(service)
    guard try readKey(service)==Data("PUBLIC-NONSECRET-HOMEBUTLER-SIGNING-PROBE".utf8) else { throw Failure.invalidPayload }
    print("PUBLIC_ACCESS_PASSED")
}
