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
        public string PortalSha256;
        public bool RuntimeBundled;
        public CudaRuntimePin Cuda;
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
            if (url.Scheme != "https" || url.Host != "huggingface.co" || bundle.Model.Size <= 0 || bundle.Runtime.Size <= 0)
                throw new InvalidDataException("The model download pin is invalid.");
            if (!bundle.RuntimeBundled)
            {
                var runtimeUrl = new Uri(bundle.Runtime.Url);
                if (runtimeUrl.Scheme != "https" || runtimeUrl.Host != "github.com") throw new InvalidDataException("The runtime download pin is invalid.");
            }
            else if (bundle.Files.Length != 1 || bundle.Runtime.File != "llama-server.exe" || bundle.Files[0].Sha256 != bundle.Runtime.Sha256)
                throw new InvalidDataException("The packaged native CPU runtime manifest is invalid.");
            if (bundle.Cuda != null) CudaRuntimeDownload.ValidatePin(bundle.Cuda);
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
    // A retained Windows Job handle owns exactly the child we create. Closing the
    // handle also cleans up after an application crash; no process-name/PID search.
    public sealed partial class ChildJob : IDisposable
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
        [DllImport("kernel32.dll", SetLastError = true)] static extern uint ResumeThread(IntPtr thread);
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
        { return StartCore(start, null); }
        Process StartCore(ProcessStartInfo start, IntPtr[] inherited)
        {
            // Windows 10+ can assign the Job inside CreateProcess, before the
            // child can execute. There is no start-then-assign crash window.
            IntPtr bytes = IntPtr.Zero;
            int count = inherited == null ? 1 : 2;
            InitializeProcThreadAttributeList(IntPtr.Zero, count, 0, ref bytes);
            IntPtr attributes = Marshal.AllocHGlobal(bytes);
            IntPtr jobValue = Marshal.AllocHGlobal(IntPtr.Size);
            IntPtr environment = IntPtr.Zero;
            IntPtr handles = IntPtr.Zero;
            bool initialized = false;
            var info = new ProcessInformation();
            try
            {
                if (!InitializeProcThreadAttributeList(attributes, count, 0, ref bytes)) throw new System.ComponentModel.Win32Exception();
                initialized = true;
                Marshal.WriteIntPtr(jobValue, handle.DangerousGetHandle());
                if (!UpdateProcThreadAttribute(attributes, 0, new IntPtr(0x0002000D), jobValue, new IntPtr(IntPtr.Size), IntPtr.Zero, IntPtr.Zero))
                    throw new System.ComponentModel.Win32Exception(); // PROC_THREAD_ATTRIBUTE_JOB_LIST
                var entries = start.EnvironmentVariables.Keys.Cast<string>().OrderBy(name => name, StringComparer.OrdinalIgnoreCase)
                    .Select(name => name + "=" + start.EnvironmentVariables[name]);
                environment = Marshal.StringToHGlobalUni(String.Join("\0", entries) + "\0\0");
                var startup = new StartupInfoEx { Attributes = attributes };
                startup.Startup.Size = Marshal.SizeOf(startup);
                if (inherited != null)
                {
                    handles = Marshal.AllocHGlobal(IntPtr.Size * inherited.Length);
                    Marshal.Copy(inherited, 0, handles, inherited.Length);
                    if (!UpdateProcThreadAttribute(attributes, 0, new IntPtr(0x00020002), handles,
                        new IntPtr(IntPtr.Size * inherited.Length), IntPtr.Zero, IntPtr.Zero)) throw new System.ComponentModel.Win32Exception();
                    startup.Startup.Flags = 0x100; // STARTF_USESTDHANDLES; explicit handle allowlist only.
                    startup.Startup.StdInput = inherited[0];
                    startup.Startup.StdOutput = startup.Startup.StdError = inherited[1];
                }
                var command = new StringBuilder("\"" + start.FileName + "\" " + start.Arguments);
                if (!CreateProcess(start.FileName, command, IntPtr.Zero, IntPtr.Zero, inherited != null, 0x08080404,
                    environment, start.WorkingDirectory, ref startup, out info)) throw new System.ComponentModel.Win32Exception();
                // Retain before resuming: short read-only probes can otherwise
                // exit before GetProcessById can obtain their handle. The Job
                // already owns the suspended child if this application crashes.
                var child = Process.GetProcessById(info.ProcessId);
                // Retain the child handle before releasing CreateProcess's handle.
                // The process object remains alive, preventing PID reuse here.
                var retained = child.Handle;
                if (ResumeThread(info.Thread) == UInt32.MaxValue)
                { int error = Marshal.GetLastWin32Error(); child.Dispose(); throw new System.ComponentModel.Win32Exception(error); }
                return child;
            }
            finally
            {
                if (info.Thread != IntPtr.Zero) CloseHandle(info.Thread);
                if (info.Process != IntPtr.Zero) CloseHandle(info.Process);
                if (initialized) DeleteProcThreadAttributeList(attributes);
                Marshal.FreeHGlobal(attributes); Marshal.FreeHGlobal(jobValue);
                if (environment != IntPtr.Zero) Marshal.FreeHGlobal(environment);
                if (handles != IntPtr.Zero) Marshal.FreeHGlobal(handles);
                GC.KeepAlive(handle);
            }
        }
        public void Dispose() { handle.Dispose(); }
    }

    public interface IPortalModel : IDisposable
    {
        bool IsRunning { get; }
        Task<string> CompleteAsync(List<Dictionary<string, string>> messages, CancellationToken token);
        Task VerifyIdleAsync();
    }
    public sealed class ModelIntegrityException : IOException
    {
        public ModelIntegrityException() : base("The selected model failed verification. Download it again.") { }
    }
    public sealed partial class LocalRuntime : IPortalModel
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
        public bool IsCuda { get; private set; }
        public string BackendDescription { get { return IsCuda ? "NVIDIA CUDA" : "CPU"; } }
        internal static int PreviewCpuThreads(int logicalProcessors)
        {
            // Keep b9014's MinGW lower-core default, but avoid oversubscribing
            // scalar inference across large Windows processor groups/sockets.
            int fallback = logicalProcessors <= 4 ? logicalProcessors : logicalProcessors / 2;
            return Math.Max(1, Math.Min(8, fallback));
        }
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
        public Task StartAsync(string runtimeDirectory, Bundle bundle, string modelPath, string stateDirectory, CancellationToken token)
        { return StartCoreAsync(runtimeDirectory, bundle, modelPath, stateDirectory, null, token); }
        public Task StartCudaAsync(string runtimeDirectory, Bundle bundle, string modelPath, string stateDirectory, CudaRuntimePin pin, CancellationToken token)
        {
            CudaRuntimeDownload.ValidatePin(pin);
            if (bundle.Files.Length != pin.Files.Length || pin.Files.Any(f => !bundle.Files.Any(b => b.Name == f.Name && b.Sha256 == f.Sha256)))
                throw new InvalidDataException("The CUDA launch manifest does not match the checked runtime.");
            return StartCoreAsync(runtimeDirectory, bundle, modelPath, stateDirectory, pin, token);
        }
        async Task StartCoreAsync(string runtimeDirectory, Bundle bundle, string modelPath, string stateDirectory, CudaRuntimePin cuda, CancellationToken token)
        {
            if (child != null) throw new InvalidOperationException("The local model is already running.");
            RuntimeDownload.ValidateForLaunch(runtimeDirectory, bundle);
            foreach (var pin in bundle.Files)
            {
                Bundle.SafeName(pin.Name);
                string path = Path.Combine(runtimeDirectory, pin.Name);
                if (!System.IO.File.Exists(path) || !await ModelDownload.MatchesAsync(path, new FileInfo(path).Length, pin.Sha256, token))
                    throw new InvalidDataException("The application runtime failed verification. Extract the complete ZIP again.");
            }
            if (!await ModelDownload.MatchesAsync(modelPath, bundle.Model.Size, bundle.Model.Sha256, token))
                throw new ModelIntegrityException();
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
                ModelDownload.InspectPath(session, true);
                Directory.CreateDirectory(session, security);
                string device = "none";
                if (cuda != null)
                {
                    var hardware = await CudaRuntime.HardwareAsync(cuda, session, token);
                    string devices = await CudaRuntime.CaptureAsync(Path.Combine(runtimeDirectory, "llama-server.exe"),
                        "--list-devices", session, token, TimeSpan.FromSeconds(30));
                    device = CudaRuntime.SelectDevice(devices, hardware);
                }
                token.ThrowIfCancellationRequested();
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
                var start = cuda == null ? new ProcessStartInfo(Path.Combine(runtimeDirectory, "llama-server.exe"))
                    : CudaRuntime.StartInfo(Path.Combine(runtimeDirectory, "llama-server.exe"), "");
                string threads = PreviewCpuThreads(Environment.ProcessorCount).ToString(System.Globalization.CultureInfo.InvariantCulture);
                start.Arguments = String.Join(" ", new[] { "--model", modelPath, "--alias", Alias, "--host", "127.0.0.1",
                    "--port", Port.ToString(), "--ctx-size", "4096", "--parallel", "1", "--device", device,
                    "--n-gpu-layers", cuda == null ? "0" : "99", "--threads", threads, "--threads-batch", threads,
                    "--fit", "off", "--jinja", "--no-webui", "--api-key-file", keyPath }.Select(Argument));
                start.UseShellExecute = false;
                start.CreateNoWindow = true;
                start.WorkingDirectory = runtimeDirectory;
                foreach (string name in start.EnvironmentVariables.Keys.Cast<string>().ToArray())
                    if (name.StartsWith("LLAMA_", StringComparison.OrdinalIgnoreCase) || String.Equals(name, "HF_TOKEN", StringComparison.OrdinalIgnoreCase) || String.Equals(name, "HUGGING_FACE_HUB_TOKEN", StringComparison.OrdinalIgnoreCase))
                        start.EnvironmentVariables.Remove(name);
                string engineLog = Path.Combine(session, "engine.log");
                child = cuda == null ? job.Start(start) : job.StartCaptured(start, engineLog);
                client = new HttpClient(new HttpClientHandler { UseProxy = false, AllowAutoRedirect = false });
                client.BaseAddress = new Uri("http://127.0.0.1:" + Port + "/");
                client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", key);
                client.Timeout = TimeSpan.FromMinutes(5);
                var clock = Stopwatch.StartNew();
                while (true)
                {
                    token.ThrowIfCancellationRequested();
                    if (cuda != null && new FileInfo(engineLog).Length > 4194304)
                        throw new InvalidDataException("The CUDA startup diagnostic output exceeded its limit.");
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
                                    if (cuda != null) CudaRuntime.VerifyOffload(CudaRuntime.ReadLog(engineLog, 4194304), device);
                                    IsCuda = cuda != null;
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
        sealed class Completion
        {
            public string Text;
            public bool Truncated;
            public string Display { get { return Text + (Truncated ? "\n\n[The response reached this preview's length limit.]" : ""); } }
        }
        public async Task<string> ChatAsync(string prompt, CancellationToken token)
        {
            if (client == null || child.HasExited) throw new InvalidOperationException("Start local AI before sending a message.");
            if (String.IsNullOrWhiteSpace(prompt) || prompt.Length > 4000) throw new ArgumentException("Enter a message of up to 4,000 characters.");
            if (TurnCount >= 8) throw new InvalidOperationException("Start a new chat to keep this preview within its context limit.");
            var messages = new List<Dictionary<string, string>>(history);
            messages.Add(new Dictionary<string, string> { { "role", "user" }, { "content", prompt } });
            var answer = await RequestCompletionAsync(messages, token);
            history.Add(messages.Last());
            history.Add(new Dictionary<string, string> { { "role", "assistant" }, { "content", answer.Text } });
            return answer.Display;
        }
        public async Task<string> CompleteAsync(List<Dictionary<string, string>> messages, CancellationToken token)
        {
            if (!IsRunning || client == null) throw new InvalidOperationException("Start local AI before sending a message.");
            messages = await NativeContextBudget.FitAsync(client, messages, token);
            byte[] body = Encoding.UTF8.GetBytes(json.Serialize(new { model = Alias, messages, stream = true, max_tokens = 512,
                temperature = 0.7, chat_template_kwargs = new { enable_thinking = false } }));
            var request = (HttpWebRequest)WebRequest.Create(new Uri(client.BaseAddress, "v1/chat/completions"));
            request.Method = "POST"; request.ContentType = "application/json"; request.Accept = "text/event-stream";
            request.Headers[HttpRequestHeader.Authorization] = client.DefaultRequestHeaders.Authorization.ToString();
            request.ContentLength = body.Length; request.Proxy = null; request.AllowAutoRedirect = false; request.KeepAlive = false;
            using (var deadline = CancellationTokenSource.CreateLinkedTokenSource(token))
            {
                deadline.CancelAfter(TimeSpan.FromMinutes(5));
                using (deadline.Token.Register(request.Abort))
                {
                    try
                    {
                        using (var output = await request.GetRequestStreamAsync()) await output.WriteAsync(body, 0, body.Length, deadline.Token);
                        using (var response = (HttpWebResponse)await request.GetResponseAsync())
                        using (var input = new StreamReader(response.GetResponseStream(), new UTF8Encoding(false, true)))
                        {
                            if (response.ContentType == null || !response.ContentType.StartsWith("text/event-stream", StringComparison.OrdinalIgnoreCase))
                                throw new InvalidDataException("The local model did not return an inference stream.");
                            var answer = new StringBuilder(); string finish = null;
                            string line;
                            while ((line = await input.ReadLineAsync()) != null)
                            {
                                deadline.Token.ThrowIfCancellationRequested();
                                if (line.Length > 1024 * 1024) throw new InvalidDataException("The model stream exceeded its frame limit.");
                                if (!line.StartsWith("data:", StringComparison.Ordinal)) continue;
                                string data = line.Substring(5).TrimStart();
                                if (data == "[DONE]")
                                {
                                    if (String.IsNullOrWhiteSpace(answer.ToString()) || finish != "stop" && finish != "length")
                                        throw new InvalidDataException("The local model did not complete a text answer.");
                                    return new Completion { Text = answer.ToString(), Truncated = finish == "length" }.Display;
                                }
                                var frame = json.DeserializeObject(data) as Dictionary<string, object>;
                                var choices = frame != null && frame.ContainsKey("choices") ? frame["choices"] as object[] : null;
                                if (frame == null || frame.ContainsKey("error") || choices == null) throw new InvalidDataException("The model stream failed.");
                                if (choices.Length == 0) continue; // A terminal usage-only frame.
                                var choice = choices[0] as Dictionary<string, object>;
                                var delta = choice != null && choice.ContainsKey("delta") ? choice["delta"] as Dictionary<string, object> : null;
                                if (delta == null) throw new InvalidDataException("The model stream contained an invalid delta.");
                                object text;
                                if (delta.TryGetValue("content", out text) && text is string) answer.Append((string)text);
                                if (answer.Length > 65536) throw new InvalidDataException("The model answer exceeded its preview limit.");
                                if (choice.ContainsKey("finish_reason") && choice["finish_reason"] != null) finish = choice["finish_reason"] as string;
                            }
                            throw new InvalidDataException("The model stream ended without completion.");
                        }
                    }
                    catch (WebException error)
                    {
                        if (deadline.IsCancellationRequested) throw new OperationCanceledException("Local inference stopped or timed out.", error, deadline.Token);
                        throw new HttpRequestException("The local model stream failed.", error);
                    }
                    catch (IOException error)
                    {
                        if (deadline.IsCancellationRequested) throw new OperationCanceledException("Local inference stopped or timed out.", error, deadline.Token);
                        throw;
                    }
                }
            }
        }
        async Task<Completion> RequestCompletionAsync(List<Dictionary<string, string>> messages, CancellationToken token)
        {
            if (!IsRunning || client == null) throw new InvalidOperationException("Start local AI before sending a message.");
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
                return new Completion { Text = answer, Truncated = choice.ContainsKey("finish_reason") && (string)choice["finish_reason"] == "length" };
            }
        }
        public async Task VerifyIdleAsync()
        {
            // b9014 /slots reports is_processing for the single owned slot.
            // HTTP cancellation alone is not evidence that inference stopped.
            using (var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(10)))
            {
                while (true)
                {
                    if (!IsRunning) return;
                    if (!await IsProcessingAsync(deadline.Token)) return;
                    await Task.Delay(100, deadline.Token);
                }
            }
        }
        public async Task<bool> IsProcessingAsync(CancellationToken token)
        {
            using (var deadline = CancellationTokenSource.CreateLinkedTokenSource(token))
            {
                deadline.CancelAfter(TimeSpan.FromSeconds(2));
                using (var response = await client.GetAsync("slots", deadline.Token))
                {
                    response.EnsureSuccessStatusCode();
                    var slots = json.DeserializeObject(await response.Content.ReadAsStringAsync()) as object[];
                    var slot = slots != null && slots.Length == 1 ? slots[0] as Dictionary<string, object> : null;
                    if (slot == null || !slot.ContainsKey("is_processing") || !(slot["is_processing"] is bool))
                        throw new InvalidDataException("The owned inference slot could not be inspected.");
                    return (bool)slot["is_processing"];
                }
            }
        }
        public void Dispose()
        {
            IsCuda = false;
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
