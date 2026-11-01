# Pester 5 tests for the pure helpers in ../ods-native-lib.ps1.
# Run: Invoke-Pester -Path ods/installers/windows/native/tests -CI
# They touch no filesystem state, so they also run on Linux/macOS pwsh.

BeforeAll {
    . (Join-Path (Split-Path -Parent $PSScriptRoot) 'ods-native-lib.ps1')
    $script:paths = Get-ODSNativePaths -InstallRoot 'C:\Users\Ann Lee\AppData\Local\Programs\ODS\' `
        -LocalAppData 'C:\Users\Ann Lee\AppData\Local' -StartupFolder 'C:\Users\Ann Lee\Startup'
}

Describe 'Get-ODSNativePaths' {
    It 'keeps state in %LOCALAPPDATA%\ODS\Native and the bundle under the install root' {
        $paths.InstallRoot | Should -Be 'C:\Users\Ann Lee\AppData\Local\Programs\ODS'
        $paths.StateRoot | Should -Be 'C:\Users\Ann Lee\AppData\Local\ODS\Native'
        $paths.OdsRoot | Should -Be 'C:\Users\Ann Lee\AppData\Local\ODS'
        $paths.Pythonw | Should -Be 'C:\Users\Ann Lee\AppData\Local\Programs\ODS\python\pythonw.exe'
        $paths.LlamaKey | Should -Be 'C:\Users\Ann Lee\AppData\Local\ODS\Native\secrets\llama-server-api-key'
        $paths.DashboardKey | Should -Be 'C:\Users\Ann Lee\AppData\Local\ODS\Native\secrets\dashboard-api-key'
        $paths.StopFile | Should -Be 'C:\Users\Ann Lee\AppData\Local\ODS\Native\run\supervisor.stop'
        $paths.RecordPath | Should -Be 'C:\Users\Ann Lee\AppData\Local\ODS\Native\run\supervisor.json'
        $paths.StartupScript | Should -Be 'C:\Users\Ann Lee\Startup\ods-native.vbs'
    }

    It 'rejects relative roots and quotes' {
        { Get-ODSNativePaths -InstallRoot 'ODS' -LocalAppData 'C:\x' } | Should -Throw
        { Get-ODSNativePaths -InstallRoot 'C:\a"b' -LocalAppData 'C:\x' } | Should -Throw
    }
}

Describe 'New-ODSNativeSupervisorConfig' {
    It 'enables llama-server with the model and binds loopback' {
        $config = New-ODSNativeSupervisorConfig -Paths $paths -ModelPath 'D:\models\tiny.gguf' -CtxSize 4096
        $config.schema_version | Should -Be 1
        $config.host | Should -Be '127.0.0.1'
        $config.services['llama-server'].enabled | Should -BeTrue
        $config.services['llama-server'].model_path | Should -Be 'D:\models\tiny.gguf'
        $config.services['llama-server'].ctx_size | Should -Be 4096
        $config.services['llama-server'].port | Should -Be 8080
        $config.services['dashboard-api'].port | Should -Be 3002
        $config.services['dashboard-api'].python_exe | Should -Be $paths.Python
    }

    It 'disables llama-server without a model' {
        $config = New-ODSNativeSupervisorConfig -Paths $paths
        $config.services['llama-server'].enabled | Should -BeFalse
        (@($config.services['llama-server'].Keys) -join ',') | Should -Be 'enabled'
    }

    It 'uses only the keys the supervisor accepts' {
        $config = New-ODSNativeSupervisorConfig -Paths $paths -ModelPath 'D:\m.gguf'
        (@($config.Keys) -join ',') | Should -Be 'schema_version,install_root,state_root,host,services,restart,logs'
        foreach ($key in $config.services['llama-server'].Keys) {
            $key | Should -BeIn @('enabled', 'port', 'model_path', 'ctx_size', 'exe', 'extra_args', 'health_timeout_seconds')
        }
        foreach ($key in $config.services['dashboard-api'].Keys) {
            $key | Should -BeIn @('enabled', 'port', 'python_exe', 'app_dir', 'env', 'health_timeout_seconds')
        }
    }

    It 'never sets a supervisor-managed variable or a secret in the dashboard env' {
        $dashboardEnv = (New-ODSNativeSupervisorConfig -Paths $paths).services['dashboard-api'].env
        foreach ($key in $dashboardEnv.Keys) {
            $key | Should -Not -BeIn @('DASHBOARD_API_KEY', 'LLAMA_SERVER_API_KEY', 'DASHBOARD_API_PORT', 'ODS_INSTALL_DIR', 'ODS_DATA_DIR')
        }
    }

    It 'round-trips through JSON and back to the model path' {
        $json = ConvertTo-ODSNativeConfigJson -Config (New-ODSNativeSupervisorConfig -Paths $paths -ModelPath 'D:\m.gguf')
        $parsed = $json | ConvertFrom-Json
        Get-ODSNativeConfigModelPath -Config $parsed | Should -Be 'D:\m.gguf'
        $parsed.services.'llama-server'.extra_args.Count | Should -Be 0
        $disabled = ConvertTo-ODSNativeConfigJson -Config (New-ODSNativeSupervisorConfig -Paths $paths) | ConvertFrom-Json
        Get-ODSNativeConfigModelPath -Config $disabled | Should -Be ''
        Get-ODSNativeConfigModelPath -Config $null | Should -Be ''
    }
}

Describe 'supervisor command line and ownership' {
    It 'quotes the config path' {
        Get-ODSNativeSupervisorArguments -Paths $paths |
            Should -Be '-m supervisor --config "C:\Users\Ann Lee\AppData\Local\ODS\Native\supervisor.json"'
    }

    It 'accepts only our interpreter with our exact arguments' {
        $line = '"{0}" {1}' -f $paths.Pythonw, (Get-ODSNativeSupervisorArguments -Paths $paths)
        Test-ODSNativeSupervisorCommandLine -ExecutablePath $paths.Pythonw -CommandLine $line -Paths $paths | Should -BeTrue
        $unquoted = '{0} {1}' -f $paths.Pythonw, (Get-ODSNativeSupervisorArguments -Paths $paths)
        Test-ODSNativeSupervisorCommandLine -ExecutablePath $paths.Pythonw -CommandLine $unquoted -Paths $paths | Should -BeTrue
        Test-ODSNativeSupervisorCommandLine -ExecutablePath 'C:\Python312\pythonw.exe' -CommandLine $line -Paths $paths | Should -BeFalse
        Test-ODSNativeSupervisorCommandLine -ExecutablePath $paths.Pythonw -CommandLine ($line + ' --extra') -Paths $paths | Should -BeFalse
        Test-ODSNativeSupervisorCommandLine -ExecutablePath $paths.Pythonw -CommandLine ('"{0}" -c "print(1)"' -f $paths.Pythonw) -Paths $paths | Should -BeFalse
        Test-ODSNativeSupervisorCommandLine -ExecutablePath '' -CommandLine '' -Paths $paths | Should -BeFalse
    }

    It 'recognises only the task action install-state registers' {
        $arguments = Get-ODSNativeSupervisorArguments -Paths $paths
        Test-ODSNativeTaskAction -Execute $paths.Pythonw -Arguments $arguments -Paths $paths | Should -BeTrue
        Test-ODSNativeTaskAction -Execute ('"{0}"' -f $paths.Pythonw) -Arguments $arguments -Paths $paths | Should -BeTrue
        Test-ODSNativeTaskAction -Execute 'powershell.exe' -Arguments $arguments -Paths $paths | Should -BeFalse
        Test-ODSNativeTaskAction -Execute $paths.Pythonw -Arguments '-m other' -Paths $paths | Should -BeFalse
    }
}

Describe 'Startup-folder fallback script' {
    It 'runs pythonw hidden with the doubled VBScript quotes and is recognised as ours' {
        $content = Get-ODSNativeStartupScriptContent -Paths $paths
        $content | Should -Match ([regex]::Escape('WshShell.Run """C:\Users\Ann Lee\AppData\Local\Programs\ODS\python\pythonw.exe"" -m supervisor --config ""C:\Users\Ann Lee\AppData\Local\ODS\Native\supervisor.json""", 0, False'))
        $content | Should -Not -Match 'powershell'
        Test-ODSNativeStartupScriptContent -Content $content | Should -BeTrue
        Test-ODSNativeStartupScriptContent -Content "Set x = 1`r`n" | Should -BeFalse
        Test-ODSNativeStartupScriptContent -Content '' | Should -BeFalse
    }
}

Describe 'ConvertTo-ODSNativeSecretText' {
    It 'hex-encodes 32 bytes' {
        $bytes = [byte[]](0..31)
        $text = ConvertTo-ODSNativeSecretText -Bytes $bytes
        $text.Length | Should -Be 64
        $text | Should -Match '^[0-9a-f]{64}$'
        $text.Substring(0, 6) | Should -Be '000102'
    }

    It 'refuses short input' {
        { ConvertTo-ODSNativeSecretText -Bytes ([byte[]](1..16)) } | Should -Throw
    }
}

Describe 'Get-ODSNativeRemovalPlan' {
    It 'keeps models and data by default' {
        $plan = @(Get-ODSNativeRemovalPlan -Paths $paths)
        $plan | Should -Contain $paths.SecretsDir
        $plan | Should -Contain $paths.ConfigPath
        $plan | Should -Not -Contain $paths.ModelsDir
        $plan | Should -Not -Contain $paths.DataDir
        $plan | Should -Not -Contain $paths.StateRoot
    }

    It 'removes the whole native state folder, never %LOCALAPPDATA%\ODS, with -RemoveData' {
        (@(Get-ODSNativeRemovalPlan -Paths $paths -RemoveData) -join ',') | Should -Be $paths.StateRoot
    }
}
