// SPDX-License-Identifier: Apache-2.0
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using OdsDesktop;

static class CudaRuntimeLaunchTests
{
    static int checks;
    static void Check(bool value,string text) { if(!value) throw new Exception(text); checks++; Console.WriteLine("PASS "+text); }
    static void Refuses(Action action,string text) { bool failed=false; try {action();} catch(InvalidDataException){failed=true;} Check(failed,text); }
    static CudaRuntimePin Pin() { return new CudaRuntimePin {MinimumDriverVersion="551.78",CudaArchsByCompute=new Dictionary<string,string[]> {{"8.6",new[]{"860"}}}}; }
    static string Hash(string value) {using(var hash=SHA256.Create())return BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(value))).Replace("-","").ToLowerInvariant();}
    static async Task Integrity(string root)
    {
        string engine=Path.Combine(root,"engine"),state=Path.Combine(root,"no-session-state"),model=Path.Combine(root,"model.gguf");Directory.CreateDirectory(engine);
        File.WriteAllText(Path.Combine(engine,"llama-server.exe"),"good");File.WriteAllText(Path.Combine(engine,"ggml-cuda.dll"),"good");File.WriteAllText(model,"evil");
        var pin=Pin();pin.ReleaseTag="b9014";pin.Flavor="win-cuda-12.4-x64";
        pin.Archives=new[]{new ModelPin {File="a.zip",Url="https://github.com/ggml-org/llama.cpp/releases/download/b9014/a.zip",Size=1,Sha256=Hash("good")},new ModelPin {File="b.zip",Url="https://github.com/ggml-org/llama.cpp/releases/download/b9014/b.zip",Size=1,Sha256=Hash("good")}};
        pin.Files=new[]{new CudaFilePin {Name="llama-server.exe",Size=4,Sha256=Hash("good")},new CudaFilePin{Name="ggml-cuda.dll",Size=4,Sha256=Hash("good")}};
        var basis=new Bundle {Model=new ModelPin {File="model.gguf",Size=4,Sha256=Hash("good")}};
        var launch=CudaRuntimeDownload.ForLaunch(basis,pin);
        using(var runtime=new LocalRuntime()) {
            bool refused=false;try {await runtime.StartCudaAsync(engine,launch,model,state,pin,CancellationToken.None);} catch(ModelIntegrityException){refused=true;}
            Check(refused&&!Directory.Exists(state),"same-size bad model refuses CUDA before hardware child/session/key creation");
            File.WriteAllText(model,"good");File.WriteAllText(Path.Combine(engine,"ggml-cuda.dll"),"evil");
            refused=false;try {await runtime.StartCudaAsync(engine,launch,model,state,pin,CancellationToken.None);} catch(InvalidDataException){refused=true;}
            Check(refused&&!Directory.Exists(state),"same-size bad CUDA DLL refuses before hardware child/session/key creation");
            File.WriteAllText(Path.Combine(engine,"ggml-cuda.dll"),"good");
            using(var canceled=new CancellationTokenSource()) {
                canceled.Cancel();refused=false;try {await runtime.StartCudaAsync(engine,launch,model,state,pin,canceled.Token);} catch(OperationCanceledException){refused=true;}
                Check(refused&&!Directory.Exists(state),"canceled CUDA launch creates no child/session/key");
            }
            launch.Files=new[]{new FilePin{Name="llama-server.exe",Sha256=Hash("good")}};
            refused=false;try {await runtime.StartCudaAsync(engine,launch,model,state,pin,CancellationToken.None);} catch(InvalidDataException){refused=true;}
            Check(refused&&!Directory.Exists(state),"mismatched CUDA census refuses before child/session/key");
        }
    }
    static async Task Run(string root)
    {
        await Integrity(root);
        var pin=Pin();
        var gpu=CudaRuntime.ParseHardware("0, NVIDIA GeForce RTX 3060, 616.92, 8.6, 12288\r\n",pin);
        Check(gpu.Name=="NVIDIA GeForce RTX 3060","exact supported hardware selected");
        Check(CudaRuntime.SelectDevice("Available devices:\n  CUDA1: NVIDIA GeForce RTX 3060 (12288 MiB, 11000 MiB free)\n",gpu)=="CUDA1","runtime index comes from exact CUDA device identity");
        Refuses(()=>CudaRuntime.SelectDevice("CUDA0: NVIDIA GeForce RTX 3060 Ti (8192 MiB)\n",gpu),"substring GPU SKU never matches");
        Refuses(()=>CudaRuntime.SelectDevice("Vulkan0: NVIDIA GeForce RTX 3060 (12288 MiB)\n",gpu),"different backend never matches");
        Refuses(()=>CudaRuntime.SelectDevice("CUDA0: NVIDIA GeForce RTX 3060 (12288 MiB)\nCUDA1: NVIDIA GeForce RTX 3060 (12288 MiB)\n",gpu),"ambiguous same-model devices refuse automatic selection");
        Refuses(()=>CudaRuntime.ParseHardware("0, NVIDIA GeForce RTX 3060, 551.77, 8.6, 12288",pin),"old driver refused");
        Refuses(()=>CudaRuntime.ParseHardware("0, NVIDIA GeForce RTX 5060, 616.92, 12.0, 8192",pin),"uncompiled compute capability refused");
        Refuses(()=>CudaRuntime.ParseHardware("0, NVIDIA GeForce RTX 3060, 616.92, 8.6, 2048",pin),"insufficient VRAM refused");
        Refuses(()=>CudaRuntime.ParseHardware("bad output",pin),"malformed hardware output refused");
        Refuses(()=>CudaRuntime.ParseHardware("0, NVIDIA GeForce RTX 3060, 616.92, 8.6, 12288\n0, NVIDIA GeForce RTX 3060, 616.92, 8.6, 12288",pin),"duplicate hardware index refused");
        Check(CudaRuntime.ParseHardware("0, NVIDIA GeForce RTX 5060, 616.92, 12.0, 8192\n1, NVIDIA GeForce RTX 3060, 616.92, 8.6, 12288",pin).Name==gpu.Name,"supported adapter found alongside unsupported adapter");
        CudaRuntime.VerifyOffload("load_tensors: offloaded 25/25 layers to GPU\nload_tensors: CUDA1 model buffer size = 1024.00 MiB\n","CUDA1");
        Check(true,"complete actual CUDA layer and model buffer offload accepted");
        Refuses(()=>CudaRuntime.VerifyOffload("offloaded 0/25 layers to GPU\nCUDA1 model buffer size = 1024 MiB","CUDA1"),"zero offload never claims CUDA ready");
        Refuses(()=>CudaRuntime.VerifyOffload("offloaded 20/25 layers to GPU\nCUDA1 model buffer size = 1024 MiB","CUDA1"),"partial offload refused in full-offload profile");
        Refuses(()=>CudaRuntime.VerifyOffload("offloaded 25/25 layers to GPU\nCUDA0 model buffer size = 1024 MiB","CUDA1"),"another device's buffer does not qualify selected CUDA device");
        string self=Assembly.GetExecutingAssembly().Location;
        string prior=Environment.GetEnvironmentVariable("CUDA_VISIBLE_DEVICES"), priorMemory=Environment.GetEnvironmentVariable("GGML_CUDA_ENABLE_UNIFIED_MEMORY"), priorToken=Environment.GetEnvironmentVariable("HF_TOKEN");
        try {
            Environment.SetEnvironmentVariable("cuda_visible_devices","7,2");
            Environment.SetEnvironmentVariable("hf_token","fixture-payload");
            Environment.SetEnvironmentVariable("GGML_CUDA_ENABLE_UNIFIED_MEMORY","1");
            string env=await CudaRuntime.CaptureAsync(self,"--env-fixture",root,CancellationToken.None,TimeSpan.FromSeconds(10));
            Check(env.Trim()=="clean","GPU probe and model shared launcher remove inherited remapping and memory overrides");
        } finally {Environment.SetEnvironmentVariable("CUDA_VISIBLE_DEVICES",prior);Environment.SetEnvironmentVariable("GGML_CUDA_ENABLE_UNIFIED_MEMORY",priorMemory);Environment.SetEnvironmentVariable("HF_TOKEN",priorToken);}
        string bounded=Path.Combine(root,"bounded.log");File.WriteAllText(bounded,new string('x',1025));
        Refuses(()=>CudaRuntime.ReadLog(bounded,1024),"diagnostic read enforces byte limit");File.Delete(bounded);
        string output=await CudaRuntime.CaptureAsync(self,"--capture-fixture",root,CancellationToken.None,TimeSpan.FromSeconds(10));
        Check(output.Contains("fixture stdout")&&output.Contains("fixture stderr"),"atomic Job child captures both output streams");
        bool timeout=false; try {await CudaRuntime.CaptureAsync(self,"--hang-fixture",root,CancellationToken.None,TimeSpan.FromMilliseconds(300));} catch(TimeoutException){timeout=true;}
        Check(timeout,"hung probe times out and closes its Job");
        using(var cancel=new CancellationTokenSource(200)) {
            bool stopped=false;try {await CudaRuntime.CaptureAsync(self,"--hang-fixture",root,cancel.Token,TimeSpan.FromSeconds(10));} catch(OperationCanceledException){stopped=true;}
            Check(stopped,"owner cancellation stops probe without readiness");
        }
        bool oversized=false;try {await CudaRuntime.CaptureAsync(self,"--large-fixture",root,CancellationToken.None,TimeSpan.FromSeconds(10));} catch(InvalidDataException){oversized=true;}
        Check(oversized,"oversized diagnostic output refuses readiness");
        Check(Directory.GetFiles(root,"probe-*.log").Length==0,"owned probe logs cleaned after success and failures");
    }
    public static int Main(string[] args)
    {
        if(args.Length>0) {
            if(args[0]=="--capture-fixture") {Console.WriteLine("fixture stdout");Console.Error.WriteLine("fixture stderr");return 0;}
            if(args[0]=="--hang-fixture") {Thread.Sleep(30000);return 0;}
            if(args[0]=="--large-fixture") {Console.Write(new string('x',300000));return 0;}
            if(args[0]=="--env-fixture") {Console.WriteLine(Environment.GetEnvironmentVariable("CUDA_VISIBLE_DEVICES")==null && Environment.GetEnvironmentVariable("GGML_CUDA_ENABLE_UNIFIED_MEMORY")==null && Environment.GetEnvironmentVariable("HF_TOKEN")==null ? "clean" : "dirty");return 0;}
        }
        string root=Path.Combine(Path.GetTempPath(),"ods-cuda-launch-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(root);
        try {Run(root).GetAwaiter().GetResult();Console.WriteLine("PASS "+checks+" CUDA launch contracts");return 0;}
        catch(Exception e) {Console.Error.WriteLine(e);return 1;}
        finally {Directory.Delete(root,true);}
    }
}
