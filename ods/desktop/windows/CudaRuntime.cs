// SPDX-License-Identifier: Apache-2.0
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Win32.SafeHandles;

namespace OdsDesktop
{
    public sealed partial class ChildJob
    {
        [StructLayout(LayoutKind.Sequential)] struct FileSecurity
        { public int Length; public IntPtr Descriptor; [MarshalAs(UnmanagedType.Bool)] public bool Inherit; }
        [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]
        static extern SafeFileHandle CreateFile(string name,uint access,uint sharing,ref FileSecurity security,uint creation,uint flags,IntPtr template);
        [DllImport("kernel32.dll",SetLastError=true)]
        static extern bool SetHandleInformation(SafeFileHandle handle,uint mask,uint flags);
        public Process StartCaptured(ProcessStartInfo start,string output)
        {
            ModelDownload.InspectPath(output,false);
            // No pipes or async readers: both streams share one diagnostic file.
            // Probe reads are bounded. Only these two handles are inherited, with atomic Job
            // assignment; cancellation/crash cannot strand an unowned probe.
            // Delete sharing permits owned-log cleanup after termination is
            // signalled while Windows finishes closing the killed child's handles.
            using(var stream=new FileStream(output,FileMode.CreateNew,FileAccess.Write,FileShare.ReadWrite|FileShare.Delete))
            {
                var security=new FileSecurity {Length=Marshal.SizeOf(typeof(FileSecurity)),Inherit=true};
                using(var input=CreateFile("NUL",0x80000000,3,ref security,3,0,IntPtr.Zero))
                {
                    if(input.IsInvalid || !SetHandleInformation(stream.SafeFileHandle,1,1)) throw new Win32Exception();
                    try { return StartCore(start,new[]{input.DangerousGetHandle(),stream.SafeFileHandle.DangerousGetHandle()}); }
                    finally {SetHandleInformation(stream.SafeFileHandle,1,0);GC.KeepAlive(input);}
                }
            }
        }
    }
    internal sealed class CudaHardware { public string Name; }
    public static class CudaRuntime
    {
        static readonly Regex Csv=new Regex(@"\A(?<index>[0-9]+),\s*(?<name>[^,\r\n]+),\s*(?<driver>[0-9]{2,4}\.[0-9]{1,3}),\s*(?<compute>[0-9]{1,2}\.[0-9]),\s*(?<memory>[0-9]+)\z",RegexOptions.CultureInvariant);
        internal static CudaHardware ParseHardware(string output,CudaRuntimePin pin)
        {
            var indexes=new HashSet<string>(StringComparer.Ordinal);
            var supported=new List<CudaHardware>();
            foreach(string line in output.Split(new[]{'\r','\n'},StringSplitOptions.RemoveEmptyEntries))
            {
                var row=Csv.Match(line.Trim());
                if(!row.Success || !indexes.Add(row.Groups["index"].Value)) throw new InvalidDataException("NVIDIA hardware information could not be verified. Choose CPU or repair the NVIDIA driver.");
                long memory;
                if(!Int64.TryParse(row.Groups["memory"].Value,NumberStyles.None,CultureInfo.InvariantCulture,out memory)) throw new InvalidDataException("NVIDIA memory information is invalid.");
                if(new Version(row.Groups["driver"].Value)<new Version(pin.MinimumDriverVersion) ||
                    !pin.CudaArchsByCompute.ContainsKey(row.Groups["compute"].Value) || memory<4096) continue;
                supported.Add(new CudaHardware {Name=row.Groups["name"].Value.Trim()});
            }
            if(supported.Count==0) throw new InvalidDataException("NVIDIA CUDA needs a supported GPU with at least 4 GB VRAM and driver "+pin.MinimumDriverVersion+" or newer. This preview's pinned CUDA 12.4 engine does not support RTX 50-series GPUs. Choose CPU to continue.");
            return supported[0];
        }
        static string Normalize(string value)
        { return Regex.Replace(Regex.Replace(value.Trim(),@"\ANVIDIA\s+","",RegexOptions.IgnoreCase),@"\s+"," ").ToUpperInvariant(); }
        internal static string SelectDevice(string output,CudaHardware gpu)
        {
            var devices=new List<string>();
            foreach(string line in output.Split(new[]{'\r','\n'},StringSplitOptions.RemoveEmptyEntries))
            {
                var match=Regex.Match(line.Trim(),@"\A(?<device>CUDA[0-9]+):\s*(?<name>.+?)\s*\([0-9]+ MiB(?:,[^\r\n]*)?\)\z",RegexOptions.CultureInvariant);
                if(match.Success && Normalize(match.Groups["name"].Value)==Normalize(gpu.Name)) devices.Add(match.Groups["device"].Value);
            }
            if(devices.Count!=1) throw new InvalidDataException("The checked CUDA engine could not identify one exact supported NVIDIA GPU. Choose CPU or review the NVIDIA driver/devices.");
            return devices[0];
        }
        internal static void VerifyOffload(string log,string device)
        {
            var layers=Regex.Matches(log,@"offloaded ([0-9]+)/([0-9]+) layers to GPU");
            var buffers=Regex.Matches(log,Regex.Escape(device)+@"\s+model buffer size\s*=\s*([0-9]+(?:\.[0-9]+)?) MiB");
            int n,total;double size;
            bool full=layers.Count==1 && Int32.TryParse(layers[0].Groups[1].Value,out n) && Int32.TryParse(layers[0].Groups[2].Value,out total) && n>0 && n==total;
            bool buffer=buffers.Cast<Match>().Any(m=>Double.TryParse(m.Groups[1].Value,NumberStyles.AllowDecimalPoint,CultureInfo.InvariantCulture,out size)&&size>0);
            if(!full || !buffer) throw new InvalidDataException("The model did not complete verified NVIDIA GPU offload. Close other GPU applications and retry, or choose CPU. No CPU fallback was started.");
        }
        internal static string ReadLog(string path,long limit)
        {
            ModelDownload.InspectPath(path,false);
            using(var stream=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.ReadWrite))
            {
                if(stream.Length>limit) throw new InvalidDataException("The native diagnostic output exceeded its limit.");
                using(var output=new MemoryStream())
                {
                    var bytes=new byte[8192];int read;
                    while((read=stream.Read(bytes,0,(int)Math.Min(bytes.Length,limit-output.Length+1)))>0)
                    {
                        if(output.Length+read>limit) throw new InvalidDataException("The native diagnostic output exceeded its limit.");
                        output.Write(bytes,0,read);
                    }
                    return new UTF8Encoding(false,true).GetString(output.ToArray());
                }
            }
        }
        internal static ProcessStartInfo StartInfo(string executable,string arguments)
        {
            var start=new ProcessStartInfo(executable,arguments) {UseShellExecute=false,CreateNoWindow=true,WorkingDirectory=Path.GetDirectoryName(executable)};
            foreach(string name in start.EnvironmentVariables.Keys.Cast<string>().ToArray())
                if(name.StartsWith("LLAMA_",StringComparison.OrdinalIgnoreCase) || name.StartsWith("GGML_",StringComparison.OrdinalIgnoreCase) ||
                    new[]{"CUDA_VISIBLE_DEVICES","CUDA_DEVICE_ORDER","HF_TOKEN","HUGGING_FACE_HUB_TOKEN"}.Contains(name,StringComparer.OrdinalIgnoreCase)) start.EnvironmentVariables.Remove(name);
            return start;
        }
        internal static async Task<string> CaptureAsync(string executable,string arguments,string directory,CancellationToken token,TimeSpan timeout)
        {
            token.ThrowIfCancellationRequested();
            ModelDownload.InspectPath(directory,true);
            string path=Path.Combine(directory,"probe-"+Guid.NewGuid().ToString("N")+".log");
            var job=new ChildJob();Process process=null;
            try
            {
                process=job.StartCaptured(StartInfo(executable,arguments),path);
                var clock=Stopwatch.StartNew();
                // HasExited can observe an exit code before native DLL detach
                // has flushed stdout. Wait for the kernel process object to be
                // signalled before reading output or closing the owning Job.
                while(!process.WaitForExit(0))
                {
                    token.ThrowIfCancellationRequested();
                    if(clock.Elapsed>timeout) throw new TimeoutException("The NVIDIA device check timed out. Choose CPU or repair the NVIDIA driver.");
                    if(new FileInfo(path).Length>262144) throw new InvalidDataException("The native diagnostic output exceeded its limit.");
                    await Task.Delay(50,token);
                }
                token.ThrowIfCancellationRequested();
                if(process.ExitCode!=0) throw new InvalidDataException("The NVIDIA device check exited with code "+process.ExitCode+". Choose CPU or repair the NVIDIA driver.");
                return ReadLog(path,262144);
            }
            finally
            {
                job.Dispose();
                if(process!=null) {try {if(!process.WaitForExit(5000)) throw new TimeoutException("The owned NVIDIA probe did not stop.");} finally {process.Dispose();}}
                if(ModelDownload.InspectPath(path,false)) File.Delete(path);
            }
        }
        internal static async Task<CudaHardware> HardwareAsync(CudaRuntimePin pin,string directory,CancellationToken token)
        {
            CudaRuntimeDownload.ValidatePin(pin);
            // Never resolve a GPU probe from the current directory or PATH.
            string executable=Path.Combine(Environment.SystemDirectory,"nvidia-smi.exe");
            if(!ModelDownload.InspectPath(executable,false)) throw new InvalidDataException("The NVIDIA driver tools are unavailable. Install a supported NVIDIA driver or choose CPU.");
            string text=await CaptureAsync(executable,"--query-gpu=index,name,driver_version,compute_cap,memory.total --format=csv,noheader,nounits",directory,token,TimeSpan.FromSeconds(20));
            return ParseHardware(text,pin);
        }
        public static async Task CheckHardwareAsync(CudaRuntimePin pin,string state,CancellationToken token)
        {
            CudaRuntimeDownload.ValidatePin(pin);token.ThrowIfCancellationRequested();
            string directory=Path.Combine(state,"gpu-check-"+Guid.NewGuid().ToString("N"));
            ModelDownload.InspectPath(directory,true);
            var sid=WindowsIdentity.GetCurrent().User;var acl=new DirectorySecurity();acl.SetOwner(sid);acl.SetAccessRuleProtection(true,false);
            acl.AddAccessRule(new FileSystemAccessRule(sid,FileSystemRights.FullControl,InheritanceFlags.ContainerInherit|InheritanceFlags.ObjectInherit,PropagationFlags.None,AccessControlType.Allow));
            Directory.CreateDirectory(directory,acl);
            try {await HardwareAsync(pin,directory,token);}
            finally {Directory.Delete(directory,false);}
        }
    }
}
