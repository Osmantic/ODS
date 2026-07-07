@{
    ExcludeRules = @(
        'PSAvoidUsingWriteHost'
        'PSAvoidUsingEmptyCatchBlock'
        'PSUseBOMForUnicodeEncodedFile'
        'PSUseShouldProcessForStateChangingFunctions'
        'PSUseApprovedVerbs'
        'PSUseDeclaredVarsMoreThanAssignments'
        'PSUseSingularNouns'
    )
    Rules = @{
        PSAvoidUsingConvertToSecureStringWithPlainText = @{
            Enable = $true
        }
    }
}
