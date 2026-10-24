// SPDX-License-Identifier: Apache-2.0
using System;
using System.IO;
using System.IO.Compression;
using System.Collections.Generic;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using OdsDesktop;

static class CudaRuntimeTests
{
    static int checks;
    static string root;
    static string Hash(byte[] bytes) { using (var hash=SHA256.Create()) return BitConverter.ToString(hash.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant(); }
    static void Check(bool value,string text) { if(!value) throw new Exception(text); checks++; Console.WriteLine("PASS "+text); }
    static byte[] Zip(params string[] pairs)
    {
        using(var memory=new MemoryStream()) {
            using(var zip=new ZipArchive(memory,ZipArchiveMode.Create,true))
                for(int i=0;i<pairs.Length;i+=2) using(var writer=new StreamWriter(zip.CreateEntry(pairs[i]).Open(),new UTF8Encoding(false))) writer.Write(pairs[i+1]);
            return memory.ToArray();
        }
    }
    static byte[] LinkZip()
    {
        using(var memory=new MemoryStream()) {
            using(var zip=new ZipArchive(memory,ZipArchiveMode.Create,true)) {
                var entry=zip.CreateEntry("ggml-cuda.dll");entry.ExternalAttributes=unchecked((int)0xa1ff0000);
                using(var writer=new StreamWriter(entry.Open(),new UTF8Encoding(false))) writer.Write("cuda");
            }
            return memory.ToArray();
        }
    }
    static CudaRuntimePin Pin(byte[] first,byte[] second,string dll)
    {
        return new CudaRuntimePin { ReleaseTag="b9014",Flavor="win-cuda-12.4-x64",MinimumDriverVersion="551.78",
            Archives=new[] {
                new ModelPin {File="engine.zip",Size=first.Length,Sha256=Hash(first),Url="https://github.com/ggml-org/llama.cpp/releases/download/b9014/engine.zip"},
                new ModelPin {File="cuda.zip",Size=second.Length,Sha256=Hash(second),Url="https://github.com/ggml-org/llama.cpp/releases/download/b9014/cuda.zip"}},
            Files=new[] {new CudaFilePin{Name="llama-server.exe",Size=6,Sha256=Hash(Encoding.UTF8.GetBytes("engine"))},
                new CudaFilePin{Name="ggml-cuda.dll",Size=Encoding.UTF8.GetByteCount(dll),Sha256=Hash(Encoding.UTF8.GetBytes(dll))}},
            CudaArchsByCompute=new Dictionary<string,string[]> {{"8.6",new[]{"860","86"}}} };
    }
    static async Task Reject(Func<Task> action,string label)
    { bool rejected=false; try { await action(); } catch(InvalidDataException) {rejected=true;} catch(IOException) {rejected=true;} catch(ArgumentException) {rejected=true;} catch(OperationCanceledException) {rejected=true;} Check(rejected,label); }
    static string State(string name) {return Path.Combine(root,name);}
    static Func<ModelPin,string,IProgress<double>,CancellationToken,Task<string>> Acquire(byte[] a,byte[] b)
    { return (pin,dir,progress,token)=> {token.ThrowIfCancellationRequested();Directory.CreateDirectory(dir);string path=Path.Combine(dir,pin.File);File.WriteAllBytes(path,pin.File=="engine.zip"?a:b);return Task.FromResult(path);}; }
    static async Task Run()
    {
        var a=Zip("llama-server.exe","engine"); var b=Zip("ggml-cuda.dll","cuda");var pin=Pin(a,b,"cuda");
        string candidate=await CudaRuntimeDownload.EnsureAsync(pin,State("good"),null,CancellationToken.None,Acquire(a,b));
        Check(File.ReadAllText(Path.Combine(candidate,"ggml-cuda.dll"))=="cuda","both verified archives publish their full file census");
        var bytes=File.ReadAllBytes(Path.Combine(candidate,"llama-server.exe"));bytes[0]^=1;
        var noTransport=new Func<ModelPin,string,IProgress<double>,CancellationToken,Task<string>>((p,d,g,t)=>{throw new Exception("cache reuse contacted transport");});
        Check(await CudaRuntimeDownload.EnsureAsync(pin,State("good"),null,CancellationToken.None,noTransport)==candidate,"valid cache reuse hashes files without acquisition");
        File.WriteAllBytes(Path.Combine(candidate,"llama-server.exe"),bytes);
        Check(await CudaRuntimeDownload.EnsureAsync(pin,State("good"),null,CancellationToken.None,noTransport)==candidate,"same-size corrupt extracted file is repaired from verified archive cache without network");
        string previous=Directory.GetDirectories(Path.GetDirectoryName(candidate),Path.GetFileName(candidate)+".previous-*")[0];
        Check(File.ReadAllBytes(Path.Combine(previous,"llama-server.exe"))[0]==bytes[0],"corrupt previous tree remains byte-for-byte available after repair");
        var b2=Zip("ggml-cuda.dll","new!");var pin2=Pin(a,b2,"new!");
        string second=await CudaRuntimeDownload.EnsureAsync(pin2,State("good"),null,CancellationToken.None,Acquire(a,b2));
        Check(second!=candidate && Directory.Exists(candidate),"second archive hash changes cache identity and preserves old candidate");
        string archiveDir=Path.Combine(State("good"),"cuda-archives",pin2.Archives[1].Sha256);
        string cachedArchive=Path.Combine(archiveDir,pin2.Archives[1].File);
        File.WriteAllBytes(cachedArchive,new byte[b2.Length]);
        // Force an extraction repair too, so a runtime hit cannot skip acquisition.
        File.WriteAllText(Path.Combine(second,"ggml-cuda.dll"),"bad!");
        Check(await CudaRuntimeDownload.EnsureAsync(pin2,State("good"),null,CancellationToken.None,Acquire(a,b2))==second,"known corrupt archive repairs through acquisition rather than permanently refusing");
        string oldArchive=Directory.GetDirectories(Path.GetDirectoryName(archiveDir),Path.GetFileName(archiveDir)+".previous-*")[0];
        Check(File.ReadAllBytes(Path.Combine(oldArchive,pin2.Archives[1].File))[0]==0,"archive repair preserves old corrupt archive bytes");
        File.WriteAllBytes(cachedArchive,new byte[b2.Length]);File.WriteAllText(Path.Combine(second,"ggml-cuda.dll"),"again");
        var repairCancel=new CancellationTokenSource();
        await Reject(async()=>{await CudaRuntimeDownload.EnsureAsync(pin2,State("good"),null,repairCancel.Token,(p,d,g,t)=>{repairCancel.Cancel();t.ThrowIfCancellationRequested();return Task.FromResult("");});},"cancelling corrupt-archive replacement leaves the old cache in place");
        Check(File.ReadAllBytes(cachedArchive)[0]==0 && File.ReadAllText(Path.Combine(second,"ggml-cuda.dll"))=="again","failed acquisition preserves both old archive and runtime bytes");
        var cpu=new Bundle {Model=new ModelPin {File="model.gguf"},Runtime=new ModelPin {File="llama-server.exe"},RuntimeBundled=true,
            Files=new[]{new FilePin{Name="llama-server.exe",Sha256=new string('a',64)}},PortalSha256=new string('b',64)};
        var launch=CudaRuntimeDownload.ForLaunch(cpu,pin);
        Check(cpu.RuntimeBundled && cpu.Runtime.File=="llama-server.exe" && cpu.Files.Length==1 && launch.Model==cpu.Model && launch.PortalSha256==cpu.PortalSha256 && !launch.RuntimeBundled && launch.Files.Length==2,"CUDA launch bundle leaves original CPU bundle untouched");
        var cancel=new CancellationTokenSource();int calls=0;
        await Reject(async()=>{await CudaRuntimeDownload.EnsureAsync(pin,State("cancel"),null,cancel.Token,(p,d,g,t)=>{if(++calls==2){cancel.Cancel();t.ThrowIfCancellationRequested();}return Acquire(a,b)(p,d,g,t);});},"cancelling second archive publishes no runtime");
        Check(!Directory.Exists(Path.Combine(State("cancel"),"cuda-runtimes")) || Directory.GetDirectories(Path.Combine(State("cancel"),"cuda-runtimes")).Length==0,"cancel leaves no staged runtime");
        await Reject(async()=>{await CudaRuntimeDownload.EnsureAsync(pin,State("badzip"),null,CancellationToken.None,Acquire(a,new byte[b.Length]));},"injected acquirer cannot bypass archive hash verification");
        foreach(var bad in new[]{Zip("../escape.dll","cuda"),Zip("GGML-CUDA.DLL","cuda","ggml-cuda.dll","cuda"),Zip("ggml-cuda.dll","cuda","extra.dll","extra"),Zip("ggml-cuda.dll","xxxx"),Zip("ggml-cuda.dll","oversize"),LinkZip(),new byte[]{1,2,3,4}}) {
            var badPin=Pin(a,bad,"cuda");
            await Reject(async()=>{await CudaRuntimeDownload.EnsureAsync(badPin,State(Guid.NewGuid().ToString("N")),null,CancellationToken.None,Acquire(a,bad));},"untrusted ZIP path/census/duplicate or file bytes are refused");
        }
        Check(!File.Exists(Path.Combine(root,"escape.dll")),"traversal writes no foreign file");
        string foreign=State("foreign");Directory.CreateDirectory(foreign);File.WriteAllText(Path.Combine(foreign,"cuda-runtimes"),"foreign");
        await Reject(async()=>{await CudaRuntimeDownload.EnsureAsync(pin,foreign,null,CancellationToken.None,Acquire(a,b));},"foreign cache-path file is refused");
        Check(File.ReadAllText(Path.Combine(foreign,"cuda-runtimes"))=="foreign","foreign path content remains untouched");
        File.WriteAllText(Path.Combine(second,"foreign.txt"),"keep");
        await Reject(async()=>{await CudaRuntimeDownload.EnsureAsync(pin2,State("good"),null,CancellationToken.None,noTransport);},"unexpected existing runtime census is refused rather than repaired");
        Check(File.ReadAllText(Path.Combine(second,"foreign.txt"))=="keep","unexpected runtime file is preserved");
        pin.Files[1].Name="LLAMA-SERVER.EXE";
        await Reject(()=>{CudaRuntimeDownload.ValidatePin(pin);return Task.FromResult(0);},"case-colliding trusted metadata is refused");
        pin.Files[1].Name="CON.dll";
        await Reject(()=>{CudaRuntimeDownload.ValidatePin(pin);return Task.FromResult(0);},"reserved Windows filename in metadata is refused");
    }
    public static int Main(string[] args) {root=Path.Combine(Path.GetTempPath(),"ods-cuda-tests-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(root);try{if(args.Length==1){var pin=new JavaScriptSerializer().Deserialize<CudaRuntimePin>(File.ReadAllText(args[0]));CudaRuntimeDownload.ValidatePin(pin);Check(pin.Files.Length==46,"canonical 46-file metadata deserializes and validates under the C# runtime contract");}Run().GetAwaiter().GetResult();Console.WriteLine("Passed "+checks+" CUDA acquisition contracts.");return 0;}catch(Exception e){Console.Error.WriteLine(e);return 1;}finally{Directory.Delete(root,true);}}
}
