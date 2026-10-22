// SPDX-License-Identifier: Apache-2.0
// Real Windows child-process tests. Network/download fixtures remain local.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Net.Sockets;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using OdsDesktop;

static class DesktopTests
{
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
    static int checks;
    static void Check(bool value, string message)
    { if (!value) throw new Exception(message); checks++; Console.WriteLine("PASS " + message); }
    static string Hash(byte[] bytes)
    { using (var sha = SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant(); }
    static ModelPin Pin(byte[] bytes)
    { return new ModelPin { File = "fixture.gguf", Name = "fixture", Size = bytes.Length, Sha256 = Hash(bytes), Url = "https://huggingface.co/fixture" }; }
    internal sealed class DownloadServer : IDisposable
    {
        public byte[] Bytes;
        public int Calls;
        public bool Stall;
        public readonly string Url;
        readonly TcpListener listener = new TcpListener(IPAddress.Loopback, 0);
        readonly List<TcpClient> connections = new List<TcpClient>();
        bool disposed;
        public DownloadServer(byte[] bytes)
        {
            Bytes = bytes; listener.Start();
            Url = "http://127.0.0.1:" + ((IPEndPoint)listener.LocalEndpoint).Port + "/model";
            Task.Run(() =>
            {
                try
                {
                    while (!disposed)
                    {
                        var connection = listener.AcceptTcpClient();
                        lock (connections) connections.Add(connection);
                        Task.Run(() => Serve(connection));
                    }
                }
                catch (SocketException) { if (!disposed) throw; }
            });
        }
        void Serve(TcpClient connection)
        {
            try
            {
                using (connection)
                using (var stream = connection.GetStream())
                {
                    var reader = new StreamReader(stream, Encoding.ASCII, false, 1024, true);
                    while (!String.IsNullOrEmpty(reader.ReadLine())) { }
                    Interlocked.Increment(ref Calls);
                    byte[] data = Bytes;
                    byte[] header = Encoding.ASCII.GetBytes("HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: " + data.Length + "\r\n\r\n");
                    stream.Write(header, 0, header.Length);
                    if (Stall) { while (stream.ReadByte() >= 0) { } }
                    else stream.Write(data, 0, data.Length);
                }
            }
            catch (IOException) { /* The client deliberately aborts cancellation cases. */ }
            catch (ObjectDisposedException) { if (!disposed) throw; }
        }
        public void Dispose()
        {
            disposed = true; listener.Stop();
            lock (connections) foreach (var connection in connections) connection.Close();
        }
    }
    sealed class CancelProgress : IProgress<double>
    {
        readonly CancellationTokenSource source;
        public CancelProgress(CancellationTokenSource source) { this.source = source; }
        public void Report(double progress) { source.Cancel(); }
    }
    static async Task Fails(Func<Task> action, string message)
    {
        bool failed = false;
        try { await action(); }
        catch (InvalidDataException) { failed = true; }
        catch (OperationCanceledException) { failed = true; }
        Check(failed, message);
    }
    static async Task Downloads(string root)
    {
        byte[] bytes = Encoding.UTF8.GetBytes("verified model fixture");
        var pin = Pin(bytes);
        using (var server = new DownloadServer(bytes))
        {
            pin.Url = server.Url;
            string models = Path.Combine(root, "models");
            string path = await ModelDownload.EnsureAsync(pin, models, null, CancellationToken.None);
            Check(File.ReadAllBytes(path).SequenceEqual(bytes), "a checked download is published");
            await ModelDownload.EnsureAsync(pin, models, null, CancellationToken.None);
            Check(server.Calls == 1, "a verified cache works offline without another request");
            File.WriteAllText(path, "old cache, preserve until verified replacement");
            server.Bytes = Encoding.UTF8.GetBytes("short");
            await Fails(() => ModelDownload.EnsureAsync(pin, models, null, CancellationToken.None), "truncated download is refused");
            Check(File.ReadAllText(path).StartsWith("old cache"), "failed replacement preserves the existing file");
            server.Bytes = Enumerable.Repeat((byte)'x', bytes.Length).ToArray();
            await Fails(() => ModelDownload.EnsureAsync(pin, models, null, CancellationToken.None), "same-size wrong checksum is refused");
            Check(Directory.GetFiles(models, "*.partial-*").Length == 0, "failed downloads leave no partial model");
            server.Bytes = bytes;
            await ModelDownload.EnsureAsync(pin, models, null, CancellationToken.None);
            Check(File.ReadAllBytes(path).SequenceEqual(bytes), "a damaged cache is repaired with a verified replacement");
            server.Bytes = new byte[2097152];
            var cancelPin = Pin(server.Bytes); cancelPin.Url = server.Url;
            using (var cancel = new CancellationTokenSource())
                await Fails(() => ModelDownload.EnsureAsync(cancelPin, Path.Combine(root, "cancelled"),
                    new CancelProgress(cancel), cancel.Token), "cancel during transfer is propagated");
            Check(Directory.GetFiles(Path.Combine(root, "cancelled")).Length == 0, "cancelled transfer publishes nothing");
            server.Stall = true;
            var watch = Stopwatch.StartNew();
            using (var cancel = new CancellationTokenSource(350))
                await Fails(() => ModelDownload.EnsureAsync(cancelPin, Path.Combine(root, "stalled"), null, cancel.Token),
                    "Cancel aborts a real HTTP response stalled after its headers");
            Check(watch.ElapsedMilliseconds < 3000, "a stalled body releases Cancel promptly on .NET Framework");
            Check(Directory.GetFiles(Path.Combine(root, "stalled")).Length == 0, "stalled cancellation leaves no partial file");
        }
    }
    static async Task RuntimeDownloads(string root)
    {
        string package = Path.Combine(root, "runtime-download-package");
        Bundle bundle = FixtureBundle(package, "fixture");
        byte[] bytes;
        using (var memory = new MemoryStream())
        {
            using (var zip = new ZipArchive(memory, ZipArchiveMode.Create, true))
            {
                var entry = zip.CreateEntry("llama-server.exe");
                using (var output = entry.Open())
                {
                    byte[] executable = File.ReadAllBytes(Path.Combine(package, "runtime", "llama-server.exe"));
                    output.Write(executable, 0, executable.Length);
                }
                using (var output = zip.CreateEntry("../foreign-sentinel").Open()) output.WriteByte(1);
            }
            bytes = memory.ToArray();
        }
        using (var server = new DownloadServer(bytes))
        {
            bundle.Runtime = Pin(bytes); bundle.Runtime.File = "fixture.zip"; bundle.Runtime.Url = server.Url;
            string state = Path.Combine(root, "runtime-download-state");
            string native = await RuntimeDownload.EnsureAsync(bundle, state, null, CancellationToken.None);
            Check(await RuntimeDownload.MatchesAsync(native, bundle, CancellationToken.None), "downloaded runtime is extracted and verified before use");
            Check(!File.Exists(Path.Combine(state, "runtimes", "foreign-sentinel")), "extraction writes only manifest filenames, never archive traversal entries");
            await RuntimeDownload.EnsureAsync(bundle, state, null, CancellationToken.None);
            Check(server.Calls == 1, "verified runtime cache works without another network request");
            File.AppendAllText(Path.Combine(native, "llama-server.exe"), "damaged");
            await RuntimeDownload.EnsureAsync(bundle, state, null, CancellationToken.None);
            Check(await RuntimeDownload.MatchesAsync(native, bundle, CancellationToken.None), "damaged runtime is replaced from the verified archive cache");
            Check(Directory.GetDirectories(Path.GetDirectoryName(native), "*.previous-*").Length == 1, "runtime repair retains the previous tree for recovery");
            File.WriteAllText(Path.Combine(native, "foreign.dll"), "unlisted library");
            Check(!await RuntimeDownload.MatchesAsync(native, bundle, CancellationToken.None), "an extra runtime DLL invalidates the executable cache");
            bundle.Files[0].Name = "../escape.exe";
            await Fails(() => RuntimeDownload.EnsureAsync(bundle, state, null, CancellationToken.None), "manifest traversal filename is refused");
        }
    }
    static Bundle FixtureBundle(string root, string modelText)
    {
        Directory.CreateDirectory(Path.Combine(root, "runtime"));
        string executable = Path.Combine(root, "runtime", "llama-server.exe");
        File.Copy(typeof(DesktopTests).Assembly.Location, executable);
        byte[] model = Encoding.UTF8.GetBytes(modelText);
        var bundle = new Bundle { Model = Pin(model), Runtime = new ModelPin { File = "fixture.zip", Url = "https://github.com/fixture", Size = 1 },
            Files = new[] { new FilePin { Name = "llama-server.exe", Sha256 = Hash(File.ReadAllBytes(executable)) } } };
        File.WriteAllBytes(Path.Combine(root, bundle.Model.File), model);
        File.WriteAllText(Path.Combine(root, "bundle.json"), Json.Serialize(bundle));
        return bundle;
    }
    static bool Gone(int pid)
    {
        try { using (var process = Process.GetProcessById(pid)) return process.HasExited || process.WaitForExit(5000); }
        catch (ArgumentException) { return true; }
    }
    static async Task Processes(string root)
    {
        string package = Path.Combine(root, "package with spaces and Unicode đ");
        Bundle bundle = FixtureBundle(package, "fixture");
        var foreign = new TcpListener(IPAddress.Loopback, 0); foreign.Start();
        try
        {
            string inherited = Environment.GetEnvironmentVariable("LLAMA_ARG_TOOLS");
            Environment.SetEnvironmentVariable("LLAMA_ARG_TOOLS", "all");
            int pid;
            string session;
            try
            {
                using (var runtime = new LocalRuntime())
                {
                    await runtime.StartAsync(Path.Combine(package, "runtime"), bundle, Path.Combine(package, bundle.Model.File), root, CancellationToken.None);
                    pid = runtime.ProcessId; session = runtime.SessionDirectory;
                    Check(runtime.IsRunning, "real fixture child starts with poisoned LLAMA environment removed");
                    Check(new DirectoryInfo(session).GetAccessControl().AreAccessRulesProtected, "API-key directory has protected Windows ACLs");
                    using (var unauth = new HttpClient(new HttpClientHandler { UseProxy = false }))
                    using (var response = await unauth.GetAsync("http://127.0.0.1:" + runtime.Port + "/v1/models"))
                        Check(response.StatusCode == HttpStatusCode.Unauthorized, "model endpoint refuses a request without its session key");
                    string answer = await runtime.ChatAsync("Xin chào", CancellationToken.None);
                    Check(answer == "Answer: Xin chào", "chat crosses the authenticated HTTP/UTF-8 boundary");
                    Check(runtime.TurnCount == 1, "a completed answer commits one conversation turn");
                    await Fails(() => runtime.ChatAsync("empty", CancellationToken.None), "empty answer is an explicit failure");
                    Check(runtime.TurnCount == 1, "failed turn does not poison conversation history");
                    string limited = await runtime.ChatAsync("truncate", CancellationToken.None);
                    Check(limited.Contains("length limit"), "length-limited answer is labelled");
                    string streamed = await runtime.CompleteAsync(new List<Dictionary<string, string>> { new Dictionary<string, string> { { "role", "user" }, { "content", "native UTF-8 chào" } } }, CancellationToken.None);
                    Check(streamed == "Answer: native UTF-8 chào", "native inference consumes the real authenticated SSE/UTF-8 contract");
                    using (var stop = new CancellationTokenSource(250))
                    {
                        await Fails(() => runtime.CompleteAsync(new List<Dictionary<string, string>> { new Dictionary<string, string> { { "role", "user" }, { "content", "native-wait" } } }, stop.Token), "native cancellation aborts an HTTP stream stalled after its headers");
                        await runtime.VerifyIdleAsync();
                        Check(runtime.IsRunning, "native stream cancellation releases the inspected slot without stopping its model child");
                    }
                    runtime.NewChat();
                    Check(runtime.TurnCount == 0, "new chat clears model context");
                    using (var cancellation = new CancellationTokenSource(250))
                        await Fails(() => runtime.ChatAsync("wait", cancellation.Token), "in-flight chat cancellation reaches the HTTP request");
                }
            }
            finally { Environment.SetEnvironmentVariable("LLAMA_ARG_TOOLS", inherited); }
            Check(Gone(pid), "normal disposal stops the exact owned child");
            Check(!Directory.Exists(session), "normal disposal removes the session key");
            Check(foreign.Server.IsBound, "normal shutdown leaves a foreign listener alone");

            string corrupt = Path.Combine(root, "corrupt");
            var corruptBundle = FixtureBundle(corrupt, "fixture");
            File.AppendAllText(Path.Combine(corrupt, "runtime", "llama-server.exe"), "changed");
            using (var runtime = new LocalRuntime())
                await Fails(() => runtime.StartAsync(Path.Combine(corrupt, "runtime"), corruptBundle, Path.Combine(corrupt, bundle.Model.File), root,
                    CancellationToken.None), "modified runtime is refused before launch");

            string tamperedModel = Path.Combine(corrupt, corruptBundle.Model.File);
            File.Copy(Path.Combine(package, "runtime", "llama-server.exe"), Path.Combine(corrupt, "runtime", "llama-server.exe"), true);
            File.WriteAllText(tamperedModel, "xxxxxxx");
            using (var runtime = new LocalRuntime())
            {
                bool refused = false;
                try { await runtime.StartAsync(Path.Combine(corrupt, "runtime"), corruptBundle, tamperedModel, root, CancellationToken.None); }
                catch (ModelIntegrityException) { refused = true; }
                Check(refused && !runtime.IsRunning && runtime.SessionDirectory == null,
                    "mandatory launch hash rejects a same-size corrupt model before creating a child or session key");
            }
            File.WriteAllText(Path.Combine(corrupt, "runtime", "foreign.dll"), "foreign");
            using (var runtime = new LocalRuntime())
            {
                await Fails(() => runtime.StartAsync(Path.Combine(corrupt, "runtime"), corruptBundle, tamperedModel, root, CancellationToken.None),
                    "every direct runtime launch refuses an unlisted DLL before model repair");
                Check(!runtime.IsRunning && runtime.SessionDirectory == null, "runtime census refusal creates no child or session key");
            }

            string wrong = Path.Combine(root, "wrong-identity");
            var wrongBundle = FixtureBundle(wrong, "wrong-identity");
            using (var runtime = new LocalRuntime())
                await Fails(() => runtime.StartAsync(Path.Combine(wrong, "runtime"), wrongBundle, Path.Combine(wrong, bundle.Model.File), root,
                    CancellationToken.None), "a healthy endpoint with the wrong model identity is refused");

            string receipt = Path.Combine(root, "child.pid");
            var info = new ProcessStartInfo(typeof(DesktopTests).Assembly.Location,
                "--holder \"" + package + "\" \"" + root + "\" \"" + receipt + "\"") { UseShellExecute = false, CreateNoWindow = true };
            using (var holder = Process.Start(info))
            {
                var wait = Stopwatch.StartNew();
                while (!File.Exists(receipt) && !holder.HasExited && wait.ElapsedMilliseconds < 10000) await Task.Delay(50);
                Check(File.Exists(receipt), "crash fixture publishes its owned child receipt");
                int heldPid = Int32.Parse(File.ReadAllText(receipt));
                holder.Kill(); holder.WaitForExit();
                Check(Gone(heldPid), "Windows Job terminates the model when its parent crashes");
            }
            Check(foreign.Server.IsBound, "parent-crash cleanup preserves a foreign listener");
        }
        finally { foreign.Stop(); }
    }
    static async Task Live(string package, string model, string state)
    {
        Bundle bundle = Bundle.Read(package);
        using (var runtime = new LocalRuntime())
        {
            var watch = Stopwatch.StartNew();
            string native = await RuntimeDownload.EnsureAsync(bundle, state, null, CancellationToken.None);
            await runtime.StartAsync(native, bundle, model, state, CancellationToken.None);
            Console.WriteLine("LIVE model ready in " + watch.Elapsed.TotalSeconds.ToString("F1") + " seconds");
            Console.WriteLine("LIVE owned child PID " + runtime.ProcessId + ", loopback port " + runtime.Port);
            string answer = await runtime.ChatAsync("What is 2+2? Answer with the number only.", CancellationToken.None);
            Console.WriteLine("LIVE answer: " + answer);
            Check(answer.Trim() == "4", "real pinned llama.cpp and catalog model answer the exact-token probe");
            string vietnamese = await runtime.ChatAsync("Hãy chào tôi bằng một câu tiếng Việt.", CancellationToken.None);
            Console.WriteLine("LIVE Vietnamese answer: " + vietnamese);
            Check(!String.IsNullOrWhiteSpace(vietnamese), "real model completes a second UTF-8 conversation turn");
            int pid = runtime.ProcessId;
            runtime.Dispose();
            Check(Gone(pid), "real model process stops when the preview closes");
        }
    }
    static void Fixture(string[] args)
    {
        string model = args[Array.IndexOf(args, "--model") + 1];
        string key = File.ReadAllText(args[Array.IndexOf(args, "--api-key-file") + 1]).Trim();
        if (Environment.GetEnvironmentVariable("LLAMA_ARG_TOOLS") != null) Environment.Exit(2);
        var server = new TcpListener(IPAddress.Loopback, Int32.Parse(args[Array.IndexOf(args, "--port") + 1]));
        server.Start();
        while (true)
        using (var connection = server.AcceptTcpClient())
        using (var stream = connection.GetStream())
        {
            var header = new List<byte>();
            while (true)
            {
                int value = stream.ReadByte(); if (value < 0) break; header.Add((byte)value);
                if (header.Count >= 4 && Encoding.ASCII.GetString(header.Skip(header.Count - 4).ToArray()) == "\r\n\r\n") break;
            }
            string headers = Encoding.ASCII.GetString(header.ToArray());
            if (headers.Length == 0) continue;
            int length = 0;
            foreach (string line in headers.Split(new[] { "\r\n" }, StringSplitOptions.None))
                if (line.StartsWith("Content-Length:", StringComparison.OrdinalIgnoreCase)) length = Int32.Parse(line.Substring(15).Trim());
            var bytes = new byte[length]; int offset = 0;
            while (offset < length) { int n = stream.Read(bytes, offset, length - offset); if (n == 0) break; offset += n; }
            string body = Encoding.UTF8.GetString(bytes);
            int code = 200;
            string result;
            if (!headers.Contains("Bearer " + key)) { code = 401; result = "{}"; }
            else if (headers.StartsWith("GET /v1/models"))
                result = Json.Serialize(new { data = new[] { new { id = File.ReadAllText(model) == "wrong-identity" ? "foreign" : LocalRuntime.Alias } } });
            else if (headers.StartsWith("GET /slots")) result = "[{\"is_processing\":false}]";
            else if (headers.StartsWith("POST /apply-template"))
                result = Json.Serialize(new { prompt = Json.Serialize(((Dictionary<string, object>)Json.DeserializeObject(body))["messages"]) });
            else if (headers.StartsWith("POST /tokenize"))
                result = Json.Serialize(new { tokens = new[] { 1, 2, 3 } });
            else
            {
                var request = (Dictionary<string, object>)Json.DeserializeObject(body);
                var messages = (object[])request["messages"];
                string prompt = (string)((Dictionary<string, object>)messages.Last())["content"];
                if ((bool)request["stream"])
                {
                    byte[] streamHeaders = Encoding.ASCII.GetBytes("HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n");
                    stream.Write(streamHeaders, 0, streamHeaders.Length);
                    string data = "data: " + Json.Serialize(new { choices = new[] { new { delta = new { content = "Answer: " + prompt }, finish_reason = prompt == "native-wait" ? null : "stop" } } }) + "\n\n";
                    if (prompt != "native-wait") data += "data: [DONE]\n\n";
                    byte[] part = Encoding.UTF8.GetBytes(data); stream.Write(part, 0, part.Length);
                    if (prompt == "native-wait")
                    {
                        try { while (stream.ReadByte() >= 0) { } }
                        catch (IOException) { /* The owner deliberately aborts this request. */ }
                    }
                    continue;
                }
                if (prompt == "wait") Thread.Sleep(15000);
                if ((bool)((Dictionary<string, object>)request["chat_template_kwargs"])["enable_thinking"] || (string)request["model"] != LocalRuntime.Alias)
                { code = 422; result = "{}"; }
                else result = Json.Serialize(new { choices = new[] { new { message = new { content = prompt == "empty" ? "" : "Answer: " + prompt },
                    finish_reason = prompt == "truncate" ? "length" : "stop" } } });
            }
            byte[] payload = Encoding.UTF8.GetBytes(result);
            byte[] prefix = Encoding.ASCII.GetBytes("HTTP/1.1 " + code + " Result\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: " + payload.Length + "\r\n\r\n");
            stream.Write(prefix, 0, prefix.Length); stream.Write(payload, 0, payload.Length);
        }
    }
    [STAThread]
    static int Main(string[] args)
    {
        ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
        try
        {
            if (args.Contains("--model")) { Fixture(args); return 0; }
            if (args.Length > 0 && args[0] == "--holder")
            {
                using (var runtime = new LocalRuntime())
                {
                    var bundle = Bundle.Read(args[1]);
                    runtime.StartAsync(Path.Combine(args[1], "runtime"), bundle, Path.Combine(args[1], bundle.Model.File), args[2], CancellationToken.None).GetAwaiter().GetResult();
                    File.WriteAllText(args[3], runtime.ProcessId.ToString());
                    Thread.Sleep(Timeout.Infinite);
                }
                return 0;
            }
            string root = Path.Combine(Path.GetTempPath(), "ods-desktop-tests-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            try
            {
                if (args.Length == 0)
                {
                    Check(!NativePrerequisites.Available(root), "missing Microsoft native runtime is detected before setup");
                    Check(NativePrerequisites.Available(Environment.SystemDirectory), "native smoke host provides the required Microsoft x64 runtime");
                }
                if (args.Length > 0 && args[0] == "--live") Live(args[1], args[2], root).GetAwaiter().GetResult();
                else if (args.Length > 0 && args[0] == "--ui-live") checks += WindowTests.Live(args[1], args[2], args[3]);
                else if (args.Length > 0 && args[0] == "--package") checks += EntrypointTests.Run(root,
                    Path.Combine(args[1], "ODS Windows Preview.exe"), Path.Combine(args[1], "bundle.json"));
                else
                {
                    Downloads(root).GetAwaiter().GetResult(); RuntimeDownloads(root).GetAwaiter().GetResult(); Processes(root).GetAwaiter().GetResult();
                    checks += PortalStateReviewTests.Run(root);
                    checks += NativeContextBudgetTests.Run().GetAwaiter().GetResult();
                    checks += ModelDownloadReviewTests.Run(root).GetAwaiter().GetResult();
                    checks += NativePortalTests.Run(root).GetAwaiter().GetResult();
                    checks += WindowTests.Run(root, FixtureBundle);
                    string package = Path.Combine(root, "entrypoint-fixture");
                    FixtureBundle(package, "fixture");
                    checks += EntrypointTests.Run(root, Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "ODS Windows Preview.exe"),
                        Path.Combine(package, "bundle.json"));
                }
                Console.WriteLine("PASS " + checks + " Windows native desktop checks"); return 0;
            }
            finally { Directory.Delete(root, true); }
        }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }
}
