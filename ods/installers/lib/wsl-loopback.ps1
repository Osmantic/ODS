# ods/installers/lib/wsl-loopback.ps1
# Reusable transport component: Ods.WslLoopbackBridge (C#5 / .NET Framework 4.x, WinPS 5.1).
# Only defines Initialize-ODSWslLoopbackType. No holder integration, no execution tools.

function Initialize-ODSWslLoopbackType {
    [CmdletBinding()]
    param()

    if ('Ods.WslLoopbackBridge' -as [type]) { return }

    $src = @'
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

namespace Ods
{
    public sealed class WslLoopbackBridge : IDisposable
    {
        private const int MaxConnectionsCap = 32;
        private const int BufferSize = 65536;
        private const int JoinMs = 3000;

        private readonly string _wslExe;
        private readonly string[] _guestArgv;
        private readonly int _listenPort;
        private readonly int _maxConnections;

        private readonly object _gate = new object();
        private TcpListener _listener;
        private Thread _acceptThread;
        private bool _disposed;
        private volatile bool _shutdown;
        private volatile bool _ready;
        private int _active;
        private readonly List<Connection> _conns = new List<Connection>();
        private IntPtr _job = IntPtr.Zero;

        public WslLoopbackBridge(string wslExe, string[] guestArgv, int listenPort, int maxConnections)
        {
            if (string.IsNullOrEmpty(wslExe)) throw new ArgumentException("wslExe");
            if (guestArgv == null || guestArgv.Length == 0) throw new ArgumentException("guestArgv");
            if (listenPort < 1 || listenPort > 65535) throw new ArgumentOutOfRangeException("listenPort");
            if (maxConnections < 1) maxConnections = 1;
            if (maxConnections > MaxConnectionsCap) maxConnections = MaxConnectionsCap;

            _wslExe = wslExe;
            _guestArgv = (string[])guestArgv.Clone();
            _listenPort = listenPort;
            _maxConnections = maxConnections;
        }

        public bool Ready { get { return _ready && !_disposed; } }
        public int ActiveConnections { get { return _active; } }
        public int ListenPort { get { return _listenPort; } }

        public void Start()
        {
            lock (_gate)
            {
                if (_disposed) throw new ObjectDisposedException("WslLoopbackBridge");
                if (_listener != null) return;

                if (!TryCreateJob())
                    throw new InvalidOperationException("job object creation failed");

                var listener = new TcpListener(IPAddress.Loopback, _listenPort);
                listener.ExclusiveAddressUse = true;
                try { listener.Start(16); }
                catch
                {
                    CloseHandle(_job);
                    _job = IntPtr.Zero;
                    throw;
                }
                _listener = listener;

                _acceptThread = new Thread(AcceptLoop);
                _acceptThread.IsBackground = true;
                _acceptThread.Name = "OdsWslLoopbackAccept";
                _acceptThread.Start();
                _ready = true;
            }
        }

        private void AcceptLoop()
        {
            while (true)
            {
                TcpClient client = null;
                try { client = _listener.AcceptTcpClient(); }
                catch (SocketException) { if (_shutdown) return; continue; }
                catch (ObjectDisposedException) { return; }
                catch (InvalidOperationException) { return; }

                Connection conn = null;
                lock (_gate)
                {
                    if (_disposed || _shutdown)
                    {
                        try { client.Close(); } catch { }
                        return;
                    }
                    if (Interlocked.Increment(ref _active) > _maxConnections)
                    {
                        Interlocked.Decrement(ref _active);
                        try { client.Close(); } catch { }
                        continue;
                    }
                    conn = new Connection(this, client);
                    _conns.Add(conn);
                    Thread t = new Thread(conn.Run);
                    t.IsBackground = true;
                    t.Name = "OdsWslLoopbackConn";
                    conn.AttachThread(t);
                    t.Start();
                }
            }
        }

        internal void OnConnectionClosed(Connection c)
        {
            lock (_gate) { _conns.Remove(c); }
            Interlocked.Decrement(ref _active);
        }

        // Called under _gate. Returns null on failure.
        internal Process SpawnChild()
        {
            var psi = new ProcessStartInfo();
            psi.FileName = _wslExe;
            psi.Arguments = BuildArgv(_guestArgv);
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.RedirectStandardInput = true;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;

            var p = new Process();
            p.StartInfo = psi;
            if (!p.Start())
            {
                p.Dispose();
                return null;
            }

            bool assigned = false;
            try { assigned = AssignProcessToJobObject(_job, p.Handle); }
            catch { assigned = false; }

            if (!assigned)
            {
                try { if (!p.HasExited) p.Kill(); } catch { }
                try { p.Dispose(); } catch { }
                return null;
            }

            try
            {
                var err = p.StandardError;
                Thread et = new Thread(delegate () {
                    try {
                        var buf = new byte[4096];
                        var s = err.BaseStream;
                        while (s.Read(buf, 0, buf.Length) > 0) { }
                    } catch { }
                });
                et.IsBackground = true;
                et.Name = "OdsWslLoopbackStderrDrain";
                et.Start();
            }
            catch { }

            return p;
        }

        private static string BuildArgv(string[] argv)
        {
            var sb = new StringBuilder();
            for (int i = 0; i < argv.Length; i++)
            {
                if (i > 0) sb.Append(' ');
                sb.Append(QuoteArg(argv[i]));
            }
            return sb.ToString();
        }

        private static string QuoteArg(string a)
        {
            if (a == null) a = "";
            bool need = a.Length == 0;
            for (int i = 0; i < a.Length; i++)
            {
                char c = a[i];
                if (c == ' ' || c == '\t' || c == '"') { need = true; break; }
            }
            if (!need) return a;

            var sb = new StringBuilder();
            sb.Append('"');
            int bs = 0;
            for (int i = 0; i < a.Length; i++)
            {
                char c = a[i];
                if (c == '\\') { bs++; continue; }
                if (c == '"')
                {
                    sb.Append('\\', bs * 2 + 1);
                    sb.Append('"');
                    bs = 0;
                }
                else
                {
                    if (bs > 0) { sb.Append('\\', bs); bs = 0; }
                    sb.Append(c);
                }
            }
            if (bs > 0) sb.Append('\\', bs * 2);
            sb.Append('"');
            return sb.ToString();
        }

        public void Dispose()
        {
            Connection[] snapshot;
            lock (_gate)
            {
                if (_disposed) return;
                _disposed = true;
                _shutdown = true;
                _ready = false;
                try { if (_listener != null) _listener.Stop(); } catch { }
                snapshot = _conns.ToArray();
            }

            for (int i = 0; i < snapshot.Length; i++)
            {
                try { snapshot[i].RequestShutdown(); } catch { }
            }

            try { if (_acceptThread != null) _acceptThread.Join(JoinMs); } catch { }

            for (int i = 0; i < snapshot.Length; i++)
            {
                try { snapshot[i].Join(JoinMs); } catch { }
            }

            // Closing the job kills any remaining children, which unblocks
            // any owned client IO still waiting on child stdout.
            IntPtr job;
            lock (_gate) { job = _job; _job = IntPtr.Zero; }
            if (job != IntPtr.Zero)
            {
                try { CloseHandle(job); } catch { }
            }

            for (int i = 0; i < snapshot.Length; i++)
            {
                try { snapshot[i].Join(JoinMs); } catch { }
            }
        }

        private bool TryCreateJob()
        {
            IntPtr job = IntPtr.Zero;
            try
            {
                job = CreateJobObject(IntPtr.Zero, null);
                if (job == IntPtr.Zero) return false;
                var info = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
                info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                int len = Marshal.SizeOf(typeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION));
                IntPtr p = Marshal.AllocHGlobal(len);
                try
                {
                    Marshal.StructureToPtr(info, p, false);
                    if (!SetInformationJobObject(job, JobObjectExtendedLimitInformation, p, (uint)len))
                    {
                        CloseHandle(job);
                        return false;
                    }
                }
                finally { Marshal.FreeHGlobal(p); }
                _job = job;
                return true;
            }
            catch
            {
                if (job != IntPtr.Zero) { try { CloseHandle(job); } catch { } }
                return false;
            }
        }

        internal sealed class Connection
        {
            private readonly WslLoopbackBridge _owner;
            private readonly TcpClient _client;
            private Process _child;
            private Thread _thread;
            private volatile bool _shutdown;

            public Connection(WslLoopbackBridge owner, TcpClient client)
            {
                _owner = owner;
                _client = client;
            }

            public void AttachThread(Thread t) { _thread = t; }

            public void Run()
            {
                try
                {
                    _client.NoDelay = true;
                    _client.ReceiveBufferSize = BufferSize;
                    _client.SendBufferSize = BufferSize;

                    Process child;
                    lock (_owner._gate)
                    {
                        if (_shutdown || _owner._disposed || _owner._shutdown)
                        {
                            try { _client.Close(); } catch { }
                            return;
                        }
                        child = _owner.SpawnChild();
                        if (child == null)
                        {
                            try { _client.Close(); } catch { }
                            return;
                        }
                        _child = child;
                    }

                    var sock = _client.Client;
                    var childIn = child.StandardInput.BaseStream;
                    var childOut = child.StandardOutput.BaseStream;

                    var upDone = new ManualResetEvent(false);
                    var downDone = new ManualResetEvent(false);
                    child.EnableRaisingEvents = true;
                    child.Exited += delegate {
                        // A failed or finished helper cannot consume more input.
                        // Keep the send side open until buffered output drains.
                        try { sock.Shutdown(SocketShutdown.Receive); } catch { }
                    };
                    if (child.HasExited)
                        try { sock.Shutdown(SocketShutdown.Receive); } catch { }

                    Thread up = new Thread(delegate () {
                        try { PumpSocketToChild(sock, childIn); }
                        catch { if (!child.HasExited) RequestShutdown(); }
                        finally
                        {
                            try { upDone.Set(); } catch { }
                        }
                    });
                    up.IsBackground = true;
                    up.Name = "OdsWslLoopbackUp";

                    Thread down = new Thread(delegate () {
                        try { PumpChildToSocket(childOut, sock); }
                        catch { RequestShutdown(); }
                        finally
                        {
                            try { sock.Shutdown(SocketShutdown.Send); } catch { }
                            try { downDone.Set(); } catch { }
                        }
                    });
                    down.IsBackground = true;
                    down.Name = "OdsWslLoopbackDown";

                    up.Start();
                    down.Start();

                    upDone.WaitOne();
                    downDone.WaitOne();
                    up.Join();
                    down.Join();

                    try { child.WaitForExit(2000); } catch { }

                    try { upDone.Close(); } catch { }
                    try { downDone.Close(); } catch { }
                }
                catch { }
                finally
                {
                    try { if (_child != null && !_child.HasExited) _child.Kill(); } catch { }
                    try { if (_child != null) _child.Dispose(); } catch { }
                    try { _client.Close(); } catch { }
                    _owner.OnConnectionClosed(this);
                }
            }

            // External TCP stays raw. Frames exist only on the owned child pipe.
            // On socket EOF: send END_WRITE frame, keep child stdin open.
            // On socket error: send ABORT frame, close child stdin.
            private static void PumpSocketToChild(Socket sock, Stream childIn)
            {
                var buf = new byte[BufferSize];
                while (true)
                {
                    int n = sock.Receive(buf, 0, buf.Length, SocketFlags.None);
                    if (n == 0)
                    {
                        childIn.Write(new byte[] { 2, 0, 0, 0, 0 }, 0, 5);
                        childIn.Flush();
                        return;
                    }
                    byte[] hdr = new byte[] {
                        1, (byte)(n >> 24), (byte)(n >> 16), (byte)(n >> 8), (byte)n
                    };
                    childIn.Write(hdr, 0, hdr.Length);
                    childIn.Write(buf, 0, n);
                    childIn.Flush();
                }
            }

            private static void PumpChildToSocket(Stream childOut, Socket sock)
            {
                var hdr = new byte[5];
                var buf = new byte[BufferSize];
                while (true)
                {
                    ReadExact(childOut, hdr, 5);
                    int n = (hdr[1] << 24) | (hdr[2] << 16) | (hdr[3] << 8) | hdr[4];
                    if (hdr[0] == 2 && n == 0) return;
                    if (hdr[0] != 1 || n < 1 || n > BufferSize)
                        throw new IOException("invalid guest frame");
                    ReadExact(childOut, buf, n);
                    int off = 0;
                    while (off < n)
                    {
                        int w = sock.Send(buf, off, n - off, SocketFlags.None);
                        if (w <= 0) throw new IOException("socket closed");
                        off += w;
                    }
                }
            }

            private static void ReadExact(Stream source, byte[] buffer, int count)
            {
                int off = 0;
                while (off < count)
                {
                    int n = source.Read(buffer, off, count - off);
                    if (n == 0) throw new IOException("truncated guest frame");
                    off += n;
                }
            }

            public void RequestShutdown()
            {
                _shutdown = true;
                try { _client.Close(); } catch { }
                lock (_owner._gate)
                {
                    try { if (_child != null && !_child.HasExited) _child.Kill(); } catch { }
                    try { if (_child != null) _child.StandardInput.BaseStream.Close(); } catch { }
                }
            }

            public void Join(int ms)
            {
                try { if (_thread != null) _thread.Join(ms); } catch { }
            }
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
        private struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        {
            public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
            public IO_COUNTERS IoInfo;
            public UIntPtr ProcessMemoryLimit;
            public UIntPtr JobMemoryLimit;
            public UIntPtr PeakProcessMemoryUsed;
            public UIntPtr PeakJobMemoryUsed;
        }

        private const uint JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000;
        private const int JobObjectExtendedLimitInformation = 9;

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr CreateJobObject(IntPtr lpJobAttributes, string lpName);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool SetInformationJobObject(IntPtr hJob, int infoClass, IntPtr lpJobObjectInfo, uint cbJobObjectInfoLength);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool AssignProcessToJobObject(IntPtr hJob, IntPtr hProcess);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool CloseHandle(IntPtr hObject);
    }
}
'@

    Add-Type -TypeDefinition $src -Language CSharp -ReferencedAssemblies 'System.dll','System.Core.dll' -ErrorAction Stop
}
