# Owner-session bridge for a WSL NAT host agent when WSL localhost forwarding
# is unavailable. Docker Desktop containers reach this Windows loopback socket
# through host.docker.internal; the backend is the WSL private eth0 address.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Distro,
    [ValidateRange(1,65535)][int]$Port = 7710
)
$ErrorActionPreference = 'Stop'
if ($Distro -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$') { throw 'Invalid WSL distribution name' }

Add-Type -TypeDefinition @'
using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

public static class OdsWslAgentRelay {
    private static readonly object AddressLock = new object();
    private static IPAddress cachedAddress;

    private static IPAddress ResolveAddress(string distro) {
        var start = new ProcessStartInfo {
            FileName = Path.Combine(Environment.SystemDirectory, "wsl.exe"),
            Arguments = "-d " + distro + " --exec ip -4 -o address show dev eth0",
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true
        };
        using (var process = Process.Start(start)) {
            if (!process.WaitForExit(5000)) {
                process.Kill();
                process.WaitForExit(1000);
                throw new IOException("WSL address lookup timed out");
            }
            if (process.ExitCode != 0) throw new IOException("WSL address lookup failed");
            var matches = Regex.Matches(process.StandardOutput.ReadToEnd(), @"\binet\s+([0-9.]+)/");
            if (matches.Count != 1) throw new IOException("WSL eth0 address is ambiguous");
            foreach (Match match in matches) {
                IPAddress address;
                if (!IPAddress.TryParse(match.Groups[1].Value, out address) || address.AddressFamily != AddressFamily.InterNetwork) continue;
                var octets = address.GetAddressBytes();
                if (octets[0] == 10 || (octets[0] == 172 && octets[1] >= 16 && octets[1] <= 31) ||
                    (octets[0] == 192 && octets[1] == 168)) return address;
            }
        }
        throw new IOException("WSL did not report a private IPv4 address");
    }

    private static IPAddress GetAddress(string distro, bool refresh) {
        lock (AddressLock) {
            if (refresh || cachedAddress == null) cachedAddress = ResolveAddress(distro);
            return cachedAddress;
        }
    }

    private static void Pump(Stream from, Stream to) {
        try { from.CopyTo(to); } catch (IOException) { } catch (ObjectDisposedException) { }
    }

    private static void Handle(TcpClient incoming, string distro, int port) {
        using (incoming) {
            TcpClient backend = null;
            try {
                for (int attempt = 0; attempt != 2; attempt++) {
                    var address = GetAddress(distro, attempt != 0);
                    var candidate = new TcpClient();
                    try {
                        var connection = candidate.BeginConnect(address, port, null, null);
                        if (!connection.AsyncWaitHandle.WaitOne(3000)) throw new IOException("WSL agent connect timed out");
                        candidate.EndConnect(connection);
                        backend = candidate;
                        break;
                    } catch (Exception error) {
                        candidate.Close();
                        if (attempt != 0 || !(error is SocketException || error is IOException)) throw;
                    }
                }
                if (backend == null) return;
                using (backend) {
                    var downstream = incoming.GetStream();
                    var upstream = backend.GetStream();
                    var forward = Task.Factory.StartNew(() => Pump(downstream, upstream));
                    var reverse = Task.Factory.StartNew(() => Pump(upstream, downstream));
                    Task.WaitAny(forward, reverse);
                }
            } catch (Exception) {
                // Disconnect rather than forward to an unverified address.
            }
        }
    }

    public static void Run(string distro, int port, string mutexName) {
        using (var mutex = new Mutex(false, mutexName)) {
            bool owned;
            try { owned = mutex.WaitOne(0); }
            catch (AbandonedMutexException) { owned = true; }
            if (!owned) return;
            try {
                TcpListener listener = null;
                while (listener == null) {
                    try {
                        listener = new TcpListener(IPAddress.Loopback, port);
                        listener.Start();
                    } catch (SocketException) {
                        listener = null;
                        Thread.Sleep(1000);
                    }
                }
                try {
                    while (true) {
                        var client = listener.AcceptTcpClient();
                        ThreadPool.QueueUserWorkItem(state => Handle((TcpClient)state, distro, port), client);
                    }
                } finally { listener.Stop(); }
            } finally { mutex.ReleaseMutex(); }
        }
    }
}
'@

$hash = [Security.Cryptography.SHA256]::Create()
try {
    $owner = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $digest = -join ($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes("$owner`n$Distro`n$Port")) | ForEach-Object { $_.ToString('x2') })
} finally { $hash.Dispose() }
[OdsWslAgentRelay]::Run($Distro, $Port, ('Global\ODS-WSL-Agent-Relay-' + $digest.Substring(0,24)))
