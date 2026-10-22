// SPDX-License-Identifier: Apache-2.0
// A deliberately separate, opt-in local-chat preview. No installed ODS state is read.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using Microsoft.Win32.SafeHandles;

namespace OdsDesktop
{
    public sealed class ModelPin
    {
        public string Name, File, Url, Sha256;
        public long Size;
    }
    public sealed class FilePin { public string Name, Sha256; }
    public sealed class Bundle
    {
        public ModelPin Model;
        public ModelPin Runtime;
        public FilePin[] Files;
        public static Bundle Read(string directory)
        {
            var bundle = new JavaScriptSerializer().Deserialize<Bundle>(
                System.IO.File.ReadAllText(Path.Combine(directory, "bundle.json")));
            if (bundle == null || bundle.Model == null || bundle.Runtime == null || bundle.Files == null ||
                !bundle.Files.Any(f => f.Name == "llama-server.exe"))
                throw new InvalidDataException("The application package is incomplete. Extract the complete ZIP again.");
            foreach (var pin in bundle.Files) SafeName(pin.Name);
            SafeName(bundle.Model.File);
            SafeName(bundle.Runtime.File);
            var url = new Uri(bundle.Model.Url);
            var runtimeUrl = new Uri(bundle.Runtime.Url);
            if (url.Scheme != "https" || url.Host != "huggingface.co" || bundle.Model.Size <= 0 ||
                runtimeUrl.Scheme != "https" || runtimeUrl.Host != "github.com" || bundle.Runtime.Size <= 0)
                throw new InvalidDataException("The model download pin is invalid.");
            return bundle;
        }
        public static void SafeName(string name)
        {
            if (String.IsNullOrWhiteSpace(name) || name != Path.GetFileName(name) ||
                name.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0 || name == "." || name == "..")
                throw new InvalidDataException("The application package contains an invalid filename.");
        }
    }

    public static class NativePrerequisites
    {
        public const string HelpUrl = "https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist";
        public static bool Available(string systemDirectory)
        {
            // The official b9014 binaries import the MSVC v14.44 x64 runtime.
            // Check before a large download or an OS missing-DLL dialog.
            foreach (string name in new[] { "msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll" })
            {
                string path = Path.Combine(systemDirectory, name);
                if (!File.Exists(path)) return false;
                var version = FileVersionInfo.GetVersionInfo(path);
                if (new Version(version.FileMajorPart, version.FileMinorPart) < new Version(14, 44)) return false;
            }
            return true;
        }
    }
    public static class ModelDownload
    {
        public static async Task<bool> MatchesAsync(string path, long size, string sha, CancellationToken token)
        {
            if (!System.IO.File.Exists(path) || new FileInfo(path).Length != size) return false;
            using (var hash = SHA256.Create())
            using (var input = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read, 1048576, true))
            {
                var buffer = new byte[1048576];
                int read;
                while ((read = await input.ReadAsync(buffer, 0, buffer.Length, token)) > 0)
                {
                    token.ThrowIfCancellationRequested();
                    hash.TransformBlock(buffer, 0, read, buffer, 0);
                }
                hash.TransformFinalBlock(new byte[0], 0, 0);
                return String.Equals(BitConverter.ToString(hash.Hash).Replace("-", ""), sha, StringComparison.OrdinalIgnoreCase);
            }
        }

        public static async Task<string> EnsureAsync(ModelPin pin, string directory,
            IProgress<double> progress, CancellationToken token)
        {
            Bundle.SafeName(pin.File);
            Directory.CreateDirectory(directory);
            string destination = Path.Combine(directory, pin.File);
            if (await MatchesAsync(destination, pin.Size, pin.Sha256, token)) return destination;
            string partial = destination + ".partial-" + Guid.NewGuid().ToString("N");
            try
            {
                using (var deadline = CancellationTokenSource.CreateLinkedTokenSource(token))
                {
                    deadline.CancelAfter(TimeSpan.FromMinutes(30));
                    var request = (HttpWebRequest)WebRequest.Create(pin.Url);
                    request.Timeout = 30000;
                    request.ReadWriteTimeout = 30000;
                    // Framework HTTP streams cannot reliably cancel an in-flight
                    // ReadAsync with its token. Abort the owning request instead,
                    // including after response headers have arrived.
                    using (deadline.Token.Register(request.Abort))
                    using (var response = (HttpWebResponse)await OpenAsync(request, deadline.Token))
                    {
                        if (response.ContentLength >= 0 && response.ContentLength != pin.Size)
                            throw new InvalidDataException("The download size does not match the pinned model. Please try again later.");
                        using (var input = response.GetResponseStream())
                        using (var output = new FileStream(partial, FileMode.CreateNew, FileAccess.Write, FileShare.None, 1048576, true))
                        {
                            var buffer = new byte[1048576];
                            long total = 0;
                            while (true)
                            {
                                int read;
                                using (var stall = CancellationTokenSource.CreateLinkedTokenSource(deadline.Token))
                                {
                                    stall.CancelAfter(TimeSpan.FromSeconds(30));
                                    using (stall.Token.Register(request.Abort))
                                    {
                                        try { read = await input.ReadAsync(buffer, 0, buffer.Length, stall.Token); }
                                        catch (IOException error)
                                        {
                                            if (stall.IsCancellationRequested) throw new OperationCanceledException("Model download stopped or timed out.", error, stall.Token);
                                            throw;
                                        }
                                        catch (WebException error)
                                        {
                                            if (stall.IsCancellationRequested) throw new OperationCanceledException("Model download stopped or timed out.", error, stall.Token);
                                            throw;
                                        }
                                    }
                                }
                                if (read == 0) break;
                                total += read;
                                if (total > pin.Size) throw new InvalidDataException("The model download exceeded its pinned size.");
                                await output.WriteAsync(buffer, 0, read, deadline.Token);
                                if (progress != null) progress.Report((double)total / pin.Size);
                            }
                        }
                    }
                    if (!await MatchesAsync(partial, pin.Size, pin.Sha256, deadline.Token))
                        throw new InvalidDataException("The model download failed its checksum check. Please download again.");
                    deadline.Token.ThrowIfCancellationRequested();
                    // Keep an earlier cache intact until the replacement is completely verified.
                    if (System.IO.File.Exists(destination)) System.IO.File.Replace(partial, destination, null);
                    else System.IO.File.Move(partial, destination);
                    return destination;
                }
            }
            finally { if (System.IO.File.Exists(partial)) System.IO.File.Delete(partial); }
        }
        static async Task<WebResponse> OpenAsync(HttpWebRequest request, CancellationToken token)
        {
            try { return await request.GetResponseAsync(); }
            catch (WebException error)
            {
                if (token.IsCancellationRequested) throw new OperationCanceledException("Model download stopped or timed out.", error, token);
                throw;
            }
        }
    }

    // A retained Windows Job handle owns exactly the child we create. Closing the
    // handle also cleans up after an application crash; no process-name/PID search.
    public sealed class ChildJob : IDisposable
    {
        [StructLayout(LayoutKind.Sequential)] struct BasicLimits
        {
            public long PerProcessUserTimeLimit, PerJobUserTimeLimit;
            public uint LimitFlags;
            public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize;
            public uint ActiveProcessLimit;
            public UIntPtr Affinity;
            public uint PriorityClass, SchedulingClass;
        }
        [StructLayout(LayoutKind.Sequential)] struct IoCounters { public ulong A, B, C, D, E, F; }
        [StructLayout(LayoutKind.Sequential)] struct ExtendedLimits
        {
            public BasicLimits Basic;
            public IoCounters Io;
            public UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed;
        }
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern SafeFileHandle CreateJobObject(IntPtr attributes, string name);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool SetInformationJobObject(SafeFileHandle job, int infoClass, ref ExtendedLimits info, uint size);
        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)] struct StartupInfo
        {
            public int Size;
            public string Reserved, Desktop, Title;
            public int X, Y, XSize, YSize, XChars, YChars, Fill, Flags;
            public short ShowWindow, ReservedSize;
            public IntPtr ReservedBytes, StdInput, StdOutput, StdError;
        }
        [StructLayout(LayoutKind.Sequential)] struct StartupInfoEx { public StartupInfo Startup; public IntPtr Attributes; }
        [StructLayout(LayoutKind.Sequential)] struct ProcessInformation { public IntPtr Process, Thread; public int ProcessId, ThreadId; }
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool InitializeProcThreadAttributeList(IntPtr attributes, int count, int flags, ref IntPtr bytes);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool UpdateProcThreadAttribute(IntPtr attributes, uint flags, IntPtr attribute, IntPtr value, IntPtr bytes, IntPtr previous, IntPtr returned);
        [DllImport("kernel32.dll")] static extern void DeleteProcThreadAttributeList(IntPtr attributes);
        [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr value);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern bool CreateProcess(string application, StringBuilder command, IntPtr processAttributes, IntPtr threadAttributes,
            bool inheritHandles, uint flags, IntPtr environment, string directory, ref StartupInfoEx startup, out ProcessInformation process);
        readonly SafeFileHandle handle;
        public ChildJob()
        {
            handle = CreateJobObject(IntPtr.Zero, null);
            if (handle.IsInvalid) throw new System.ComponentModel.Win32Exception();
            var limits = new ExtendedLimits();
            limits.Basic.LimitFlags = 0x2000; // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if (!SetInformationJobObject(handle, 9, ref limits, (uint)Marshal.SizeOf(limits)))
            {
                int error = Marshal.GetLastWin32Error();
                handle.Dispose();
                throw new System.ComponentModel.Win32Exception(error);
            }
        }
        public Process Start(ProcessStartInfo start)
        {
            // Windows 10+ can assign the Job inside CreateProcess, before the
            // child can execute. There is no start-then-assign crash window.
            IntPtr bytes = IntPtr.Zero;
            InitializeProcThreadAttributeList(IntPtr.Zero, 1, 0, ref bytes);
            IntPtr attributes = Marshal.AllocHGlobal(bytes);
            IntPtr jobValue = Marshal.AllocHGlobal(IntPtr.Size);
            IntPtr environment = IntPtr.Zero;
            bool initialized = false;
            var info = new ProcessInformation();
            try
            {
                if (!InitializeProcThreadAttributeList(attributes, 1, 0, ref bytes)) throw new System.ComponentModel.Win32Exception();
                initialized = true;
                Marshal.WriteIntPtr(jobValue, handle.DangerousGetHandle());
                if (!UpdateProcThreadAttribute(attributes, 0, new IntPtr(0x0002000D), jobValue, new IntPtr(IntPtr.Size), IntPtr.Zero, IntPtr.Zero))
                    throw new System.ComponentModel.Win32Exception(); // PROC_THREAD_ATTRIBUTE_JOB_LIST
                var entries = start.EnvironmentVariables.Keys.Cast<string>().OrderBy(name => name, StringComparer.OrdinalIgnoreCase)
                    .Select(name => name + "=" + start.EnvironmentVariables[name]);
                environment = Marshal.StringToHGlobalUni(String.Join("\0", entries) + "\0\0");
                var startup = new StartupInfoEx { Attributes = attributes };
                startup.Startup.Size = Marshal.SizeOf(startup);
                var command = new StringBuilder("\"" + start.FileName + "\" " + start.Arguments);
                if (!CreateProcess(start.FileName, command, IntPtr.Zero, IntPtr.Zero, false, 0x08080400,
                    environment, start.WorkingDirectory, ref startup, out info)) throw new System.ComponentModel.Win32Exception();
                var child = Process.GetProcessById(info.ProcessId);
                // Retain the child handle before releasing CreateProcess's handle.
                // The process object remains alive, preventing PID reuse here.
                var retained = child.Handle;
                return child;
            }
            finally
            {
                if (info.Thread != IntPtr.Zero) CloseHandle(info.Thread);
                if (info.Process != IntPtr.Zero) CloseHandle(info.Process);
                if (initialized) DeleteProcThreadAttributeList(attributes);
                Marshal.FreeHGlobal(attributes); Marshal.FreeHGlobal(jobValue);
                if (environment != IntPtr.Zero) Marshal.FreeHGlobal(environment);
                GC.KeepAlive(handle);
            }
        }
        public void Dispose() { handle.Dispose(); }
    }

    public sealed class LocalRuntime : IDisposable
    {
        public const string Alias = "ods-desktop-preview";
        readonly JavaScriptSerializer json = new JavaScriptSerializer();
        readonly List<Dictionary<string, string>> history = new List<Dictionary<string, string>>();
        Process child;
        ChildJob job;
        HttpClient client;
        string session;
        public int Port { get; private set; }
        public int ProcessId { get { return child.Id; } }
        public int TurnCount { get { return history.Count / 2; } }
        public bool IsRunning { get { return child != null && !child.HasExited; } }
        public string SessionDirectory { get { return session; } }
        static string Argument(string text)
        {
            var result = new StringBuilder("\"");
            int slashes = 0;
            foreach (char value in text)
            {
                if (value == '\\') { slashes++; continue; }
                if (value == '"') result.Append('\\', slashes * 2 + 1);
                else result.Append('\\', slashes);
                result.Append(value);
                slashes = 0;
            }
            return result.Append('\\', slashes * 2).Append('"').ToString();
        }
        public async Task StartAsync(string runtimeDirectory, Bundle bundle, string modelPath, string stateDirectory, CancellationToken token)
        {
            if (child != null) throw new InvalidOperationException("The local model is already running.");
            foreach (var pin in bundle.Files)
            {
                Bundle.SafeName(pin.Name);
                string path = Path.Combine(runtimeDirectory, pin.Name);
                if (!System.IO.File.Exists(path) || !await ModelDownload.MatchesAsync(path, new FileInfo(path).Length, pin.Sha256, token))
                    throw new InvalidDataException("The application runtime failed verification. Extract the complete ZIP again.");
            }
            if (!await ModelDownload.MatchesAsync(modelPath, bundle.Model.Size, bundle.Model.Sha256, token))
                throw new InvalidDataException("The selected model failed verification. Download it again.");
            token.ThrowIfCancellationRequested();
            try
            {
                var sid = WindowsIdentity.GetCurrent().User;
                var security = new DirectorySecurity();
                security.SetAccessRuleProtection(true, false);
                security.SetOwner(sid);
                security.AddAccessRule(new FileSystemAccessRule(sid, FileSystemRights.FullControl,
                    InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit, PropagationFlags.None, AccessControlType.Allow));
                session = Path.Combine(stateDirectory, "sessions", Guid.NewGuid().ToString("N"));
                Directory.CreateDirectory(session, security);
                var secret = new byte[32];
                using (var rng = RandomNumberGenerator.Create()) rng.GetBytes(secret);
                string key = Convert.ToBase64String(secret);
                string keyPath = Path.Combine(session, "api-key");
                System.IO.File.WriteAllText(keyPath, key + "\n", new UTF8Encoding(false));
                var listener = new TcpListener(IPAddress.Loopback, 0);
                listener.Start();
                Port = ((IPEndPoint)listener.LocalEndpoint).Port;
                listener.Stop();
                job = new ChildJob();
                var start = new ProcessStartInfo(Path.Combine(runtimeDirectory, "llama-server.exe"));
                start.Arguments = String.Join(" ", new[] { "--model", modelPath, "--alias", Alias, "--host", "127.0.0.1",
                    "--port", Port.ToString(), "--ctx-size", "4096", "--parallel", "1", "--device", "none",
                    "--n-gpu-layers", "0", "--fit", "off", "--jinja", "--no-webui", "--api-key-file", keyPath }.Select(Argument));
                start.UseShellExecute = false;
                start.CreateNoWindow = true;
                start.WorkingDirectory = runtimeDirectory;
                foreach (string name in start.EnvironmentVariables.Keys.Cast<string>().ToArray())
                    if (name.StartsWith("LLAMA_", StringComparison.OrdinalIgnoreCase) || name == "HF_TOKEN" || name == "HUGGING_FACE_HUB_TOKEN")
                        start.EnvironmentVariables.Remove(name);
                child = job.Start(start);
                client = new HttpClient(new HttpClientHandler { UseProxy = false, AllowAutoRedirect = false });
                client.BaseAddress = new Uri("http://127.0.0.1:" + Port + "/");
                client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", key);
                client.Timeout = TimeSpan.FromMinutes(5);
                var clock = Stopwatch.StartNew();
                while (true)
                {
                    token.ThrowIfCancellationRequested();
                    if (child.HasExited) throw new InvalidOperationException("The local model exited with code " + child.ExitCode + ". Close other applications or extract the complete package again.");
                    if (clock.Elapsed > TimeSpan.FromMinutes(3)) throw new TimeoutException("The local model took too long to start. Close other applications and try again.");
                    using (var probe = CancellationTokenSource.CreateLinkedTokenSource(token))
                    {
                        probe.CancelAfter(TimeSpan.FromSeconds(2));
                        try
                        {
                            using (var response = await client.GetAsync("v1/models", probe.Token))
                            {
                                if (response.IsSuccessStatusCode)
                                {
                                    var payload = json.DeserializeObject(await response.Content.ReadAsStringAsync()) as Dictionary<string, object>;
                                    var data = payload == null ? null : payload["data"] as object[];
                                    if (data == null || data.Length != 1 || (string)((Dictionary<string, object>)data[0])["id"] != Alias)
                                        throw new InvalidDataException("The local model identity did not match this application.");
                                    return;
                                }
                                if (response.StatusCode != HttpStatusCode.ServiceUnavailable)
                                    throw new InvalidOperationException("The local model returned HTTP " + (int)response.StatusCode + " during startup.");
                            }
                        }
                        catch (HttpRequestException) { /* A new child has not bound its loopback socket yet. */ }
                        catch (OperationCanceledException) { token.ThrowIfCancellationRequested(); /* This readiness probe expired. */ }
                    }
                    await Task.Delay(250, token);
                }
            }
            catch { Dispose(); throw; }
        }
        public void NewChat() { history.Clear(); }
        public async Task<string> ChatAsync(string prompt, CancellationToken token)
        {
            if (client == null || child.HasExited) throw new InvalidOperationException("Start local AI before sending a message.");
            if (String.IsNullOrWhiteSpace(prompt) || prompt.Length > 4000) throw new ArgumentException("Enter a message of up to 4,000 characters.");
            if (TurnCount >= 8) throw new InvalidOperationException("Start a new chat to keep this preview within its context limit.");
            var messages = new List<Dictionary<string, string>>(history);
            messages.Add(new Dictionary<string, string> { { "role", "user" }, { "content", prompt } });
            string request = json.Serialize(new { model = Alias, messages = messages, stream = false, max_tokens = 512,
                temperature = 0.7, chat_template_kwargs = new { enable_thinking = false } });
            using (var content = new StringContent(request, Encoding.UTF8, "application/json"))
            using (var response = await client.PostAsync("v1/chat/completions", content, token))
            {
                if (!response.IsSuccessStatusCode)
                    throw new InvalidOperationException("The local model returned HTTP " + (int)response.StatusCode + ". Try a shorter message or start a new chat.");
                var body = json.DeserializeObject(await response.Content.ReadAsStringAsync()) as Dictionary<string, object>;
                var choices = body == null || !body.ContainsKey("choices") ? null : body["choices"] as object[];
                if (choices == null || choices.Length == 0) throw new InvalidDataException("The model returned no answer. Your message was kept so you can try again.");
                var choice = (Dictionary<string, object>)choices[0];
                var message = choice.ContainsKey("message") ? choice["message"] as Dictionary<string, object> : null;
                string answer = message != null && message.ContainsKey("content") ? message["content"] as string : null;
                if (String.IsNullOrWhiteSpace(answer)) throw new InvalidDataException("The model returned no answer. Your message was kept so you can try again.");
                token.ThrowIfCancellationRequested();
                history.Add(messages.Last());
                history.Add(new Dictionary<string, string> { { "role", "assistant" }, { "content", answer } });
                return answer + (choice.ContainsKey("finish_reason") && (string)choice["finish_reason"] == "length"
                    ? "\n\n[The response reached this preview's length limit.]" : "");
            }
        }
        public void Dispose()
        {
            if (client != null) { client.Dispose(); client = null; }
            if (job != null) { job.Dispose(); job = null; }
            if (child != null)
            {
                if (!child.HasExited && !child.WaitForExit(5000)) throw new TimeoutException("The owned local model did not stop.");
                child.Dispose(); child = null;
            }
            if (session != null && Directory.Exists(session)) Directory.Delete(session, true);
        }
    }
}
