# ============================================================================
# ODS Windows native process job
# ============================================================================
# Starts one executable suspended, assigns it to a kill-on-close Job Object,
# then resumes it. The job handle is deliberately not inherited by the child:
# if the supervisor exits, Windows closes the handle and terminates the entire
# assigned process tree. Compatible with Windows PowerShell 5.1 and pwsh.
# ============================================================================

if (-not ('ODS.NativeProcessJobSession' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;

namespace ODS
{
    public sealed class NativeProcessJobSession : IDisposable
    {
        private const uint CREATE_SUSPENDED = 0x00000004;
        private const uint CREATE_UNICODE_ENVIRONMENT = 0x00000400;
        private const uint CREATE_NO_WINDOW = 0x08000000;
        private const uint JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000;
        private const int JobObjectExtendedLimitInformation = 9;
        private const uint WAIT_OBJECT_0 = 0;
        private const uint WAIT_TIMEOUT = 258;

        [StructLayout(LayoutKind.Sequential)]
        private struct STARTUPINFO
        {
            public uint cb;
            public string lpReserved;
            public string lpDesktop;
            public string lpTitle;
            public uint dwX;
            public uint dwY;
            public uint dwXSize;
            public uint dwYSize;
            public uint dwXCountChars;
            public uint dwYCountChars;
            public uint dwFillAttribute;
            public uint dwFlags;
            public ushort wShowWindow;
            public ushort cbReserved2;
            public IntPtr lpReserved2;
            public IntPtr hStdInput;
            public IntPtr hStdOutput;
            public IntPtr hStdError;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct PROCESS_INFORMATION
        {
            public IntPtr hProcess;
            public IntPtr hThread;
            public uint dwProcessId;
            public uint dwThreadId;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct IO_COUNTERS
        {
            public ulong ReadOperationCount;
            public ulong WriteOperationCount;
            public ulong OtherOperationCount;
            public ulong ReadTransferCount;
            public ulong WriteTransferCount;
            public ulong OtherTransferCount;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct JOBOBJECT_BASIC_LIMIT_INFORMATION
        {
            public long PerProcessUserTimeLimit;
            public long PerJobUserTimeLimit;
            public uint LimitFlags;
            public UIntPtr MinimumWorkingSetSize;
            public UIntPtr MaximumWorkingSetSize;
            public uint ActiveProcessLimit;
            public UIntPtr Affinity;
            public uint PriorityClass;
            public uint SchedulingClass;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        {
            public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
            public IO_COUNTERS IoInfo;
            public UIntPtr ProcessMemoryLimit;
            public UIntPtr JobMemoryLimit;
            public UIntPtr PeakProcessMemoryUsed;
            public UIntPtr PeakJobMemoryUsed;
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr CreateJobObjectW(IntPtr attributes, string name);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool SetInformationJobObject(
            IntPtr job,
            int informationClass,
            ref JOBOBJECT_EXTENDED_LIMIT_INFORMATION information,
            uint informationLength);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool CreateProcessW(
            string applicationName,
            StringBuilder commandLine,
            IntPtr processAttributes,
            IntPtr threadAttributes,
            bool inheritHandles,
            uint creationFlags,
            IntPtr environment,
            string currentDirectory,
            ref STARTUPINFO startupInfo,
            out PROCESS_INFORMATION processInformation);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern uint ResumeThread(IntPtr thread);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool TerminateProcess(IntPtr process, uint exitCode);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool CloseHandle(IntPtr handle);

        private IntPtr jobHandle;
        private Process process;
        private bool disposed;

        private NativeProcessJobSession(IntPtr job, Process child)
        {
            jobHandle = job;
            process = child;
        }

        public Process Process
        {
            get
            {
                if (disposed) throw new ObjectDisposedException("NativeProcessJobSession");
                return process;
            }
        }

        private static Win32Exception NativeError(string operation)
        {
            return new Win32Exception(
                Marshal.GetLastWin32Error(),
                "Windows could not " + operation + " for the managed process.");
        }

        public static NativeProcessJobSession Start(
            string applicationPath,
            string commandLine,
            string workingDirectory)
        {
            if (String.IsNullOrEmpty(applicationPath)) throw new ArgumentException("An executable path is required.");
            if (String.IsNullOrEmpty(commandLine)) throw new ArgumentException("A command line is required.");
            if (String.IsNullOrEmpty(workingDirectory)) throw new ArgumentException("A working directory is required.");

            IntPtr job = IntPtr.Zero;
            PROCESS_INFORMATION created = new PROCESS_INFORMATION();
            Process child = null;
            bool resumed = false;
            try
            {
                job = CreateJobObjectW(IntPtr.Zero, null);
                if (job == IntPtr.Zero) throw NativeError("create the process job");

                JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits =
                    new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
                limits.BasicLimitInformation.LimitFlags =
                    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                if (!SetInformationJobObject(
                    job,
                    JobObjectExtendedLimitInformation,
                    ref limits,
                    (uint)Marshal.SizeOf(typeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION))))
                {
                    throw NativeError("configure the process job");
                }

                STARTUPINFO startup = new STARTUPINFO();
                startup.cb = (uint)Marshal.SizeOf(typeof(STARTUPINFO));
                if (!CreateProcessW(
                    applicationPath,
                    new StringBuilder(commandLine),
                    IntPtr.Zero,
                    IntPtr.Zero,
                    false,
                    CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW,
                    IntPtr.Zero,
                    workingDirectory,
                    ref startup,
                    out created))
                {
                    throw NativeError("start the managed process");
                }

                if (!AssignProcessToJobObject(job, created.hProcess))
                {
                    throw NativeError("assign the managed process to its job");
                }

                child = Process.GetProcessById((int)created.dwProcessId);
                IntPtr retained = child.Handle;
                if (retained == IntPtr.Zero)
                {
                    throw new InvalidOperationException("Windows returned no managed process handle.");
                }

                if (ResumeThread(created.hThread) == UInt32.MaxValue)
                {
                    throw NativeError("resume the managed process");
                }
                resumed = true;
                return new NativeProcessJobSession(job, child);
            }
            catch
            {
                if (created.hProcess != IntPtr.Zero)
                {
                    TerminateProcess(created.hProcess, 1);
                    WaitForSingleObject(created.hProcess, 60000);
                }
                if (child != null) child.Dispose();
                if (job != IntPtr.Zero) CloseHandle(job);
                throw;
            }
            finally
            {
                if (created.hThread != IntPtr.Zero) CloseHandle(created.hThread);
                if (created.hProcess != IntPtr.Zero) CloseHandle(created.hProcess);
                GC.KeepAlive(resumed);
            }
        }

        public bool Stop(int timeoutMilliseconds)
        {
            if (timeoutMilliseconds < 0) throw new ArgumentOutOfRangeException("timeoutMilliseconds");
            if (disposed) return true;
            if (jobHandle != IntPtr.Zero)
            {
                CloseHandle(jobHandle);
                jobHandle = IntPtr.Zero;
            }
            try
            {
                return process.HasExited || process.WaitForExit(timeoutMilliseconds);
            }
            catch (InvalidOperationException)
            {
                return true;
            }
        }

        public void Dispose()
        {
            if (disposed) return;
            try
            {
                Stop(60000);
            }
            finally
            {
                process.Dispose();
                disposed = true;
            }
        }
    }
}
'@
}

function ConvertTo-ODSNativeJobArgumentString {
    param([string[]]$Values)
    return (@($Values | ForEach-Object {
        '"' + [regex]::Replace([regex]::Replace([string]$_, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"'
    }) -join ' ')
}

function Start-ODSNativeJobProcess {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$ArgumentList = @(),
        [Parameter(Mandatory = $true)][string]$WorkingDirectory
    )
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw 'Native Windows process jobs require Windows.'
    }
    if (-not [IO.Path]::IsPathRooted($FilePath) -or
        -not (Test-Path -LiteralPath $FilePath -PathType Leaf)) {
        throw 'The managed executable must be an existing absolute file.'
    }
    if (-not [IO.Path]::IsPathRooted($WorkingDirectory) -or
        -not (Test-Path -LiteralPath $WorkingDirectory -PathType Container)) {
        throw 'The managed working directory must be an existing absolute directory.'
    }
    $commandLine = ConvertTo-ODSNativeJobArgumentString (@($FilePath) + @($ArgumentList))
    return [ODS.NativeProcessJobSession]::Start(
        [IO.Path]::GetFullPath($FilePath),
        $commandLine,
        [IO.Path]::GetFullPath($WorkingDirectory))
}

function Stop-ODSNativeJobProcess {
    param(
        [Parameter(Mandatory = $true)]$Session,
        [ValidateRange(0, 300000)][int]$TimeoutMilliseconds = 60000
    )
    if (-not $Session.Stop($TimeoutMilliseconds)) {
        throw "The managed Windows process tree did not exit within $TimeoutMilliseconds milliseconds."
    }
}
