// SPDX-License-Identifier: Apache-2.0
using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

namespace OdsDesktop
{
    public sealed class CudaRuntimePin
    {
        public string ReleaseTag, Flavor, MinimumDriverVersion;
        public ModelPin[] Archives;
        public CudaFilePin[] Files;
        public Dictionary<string,string[]> CudaArchsByCompute;
    }
    public sealed class CudaFilePin { public string Name, Sha256; public long Size; }

    public static class CudaRuntimeDownload
    {
        static void Name(string name)
        {
            if (String.IsNullOrEmpty(name) || name.Length > 180 || !Regex.IsMatch(name,@"\A[A-Za-z0-9][A-Za-z0-9._-]*\z") ||
                name.EndsWith(".",StringComparison.Ordinal) || Regex.IsMatch(name,@"\A(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\.|$)",RegexOptions.IgnoreCase))
                throw new InvalidDataException("The CUDA pin or ZIP contains an invalid Windows filename.");
        }
        static void Sha(string sha)
        { if (sha==null || !Regex.IsMatch(sha,@"\A[0-9a-f]{64}\z")) throw new InvalidDataException("The CUDA pin contains an invalid SHA-256."); }
        public static void ValidatePin(CudaRuntimePin pin)
        {
            if(pin==null || pin.ReleaseTag==null || !Regex.IsMatch(pin.ReleaseTag,@"\Ab[0-9]{3,6}\z") ||
                pin.Flavor==null || !Regex.IsMatch(pin.Flavor,@"\Awin-cuda-[0-9]{1,2}\.[0-9]-x64\z") ||
                pin.MinimumDriverVersion==null || !Regex.IsMatch(pin.MinimumDriverVersion,@"\A[0-9]{2,4}\.[0-9]{1,3}\z") ||
                pin.Archives==null || pin.Archives.Length!=2 || pin.Files==null || pin.Files.Length<2 || pin.Files.Length>256 ||
                pin.CudaArchsByCompute==null || pin.CudaArchsByCompute.Count==0)
                throw new InvalidDataException("The compiled CUDA runtime pin is incomplete.");
            var names=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach(var archive in pin.Archives) {
                if(archive==null) throw new InvalidDataException("Missing CUDA archive pin.");
                Name(archive.File);Sha(archive.Sha256);
                Uri url;
                if(!names.Add(archive.File) || !archive.File.EndsWith(".zip",StringComparison.Ordinal) || archive.Size<=0 || archive.Size>2147483648L ||
                    !Uri.TryCreate(archive.Url,UriKind.Absolute,out url) || url.Scheme!="https" || url.Host!="github.com" || !url.IsDefaultPort ||
                    url.UserInfo!="" || url.Query!="" || url.Fragment!="" ||
                    url.AbsolutePath!="/ggml-org/llama.cpp/releases/download/"+pin.ReleaseTag+"/"+archive.File)
                    throw new InvalidDataException("The CUDA archive pin is invalid.");
            }
            names.Clear();long total=0;
            foreach(var file in pin.Files) {
                if(file==null) throw new InvalidDataException("Missing CUDA file pin.");
                Name(file.Name);Sha(file.Sha256);
                if(!names.Add(file.Name) || file.Size<=0 || file.Size>2147483648L) throw new InvalidDataException("Invalid CUDA file census.");
                total=checked(total+file.Size);
            }
            if(total>8589934592L || !names.Contains("llama-server.exe") || !names.Contains("ggml-cuda.dll")) throw new InvalidDataException("Invalid CUDA runtime census.");
            foreach(var pair in pin.CudaArchsByCompute)
                if(!Regex.IsMatch(pair.Key,@"\A[0-9]{1,2}\.[0-9]\z") || pair.Value==null || pair.Value.Length==0 ||
                    pair.Value.Any(x=>x==null || !Regex.IsMatch(x,@"\A[0-9]{2,4}[a-z]?\z"))) throw new InvalidDataException("Invalid CUDA architecture map.");
        }
        public static Bundle ForLaunch(Bundle basis,CudaRuntimePin pin)
        {
            ValidatePin(pin);if(basis==null) throw new ArgumentNullException("basis");
            return new Bundle {Model=basis.Model,PortalSha256=basis.PortalSha256,Runtime=pin.Archives[0],RuntimeBundled=false,
                Files=pin.Files.Select(f=>new FilePin{Name=f.Name,Sha256=f.Sha256}).ToArray()};
        }
        static string Key(CudaRuntimePin pin)
        {
            var value=new StringBuilder(pin.ReleaseTag+"\n"+pin.Flavor+"\n");
            foreach(var a in pin.Archives) value.Append(a.File).Append(':').Append(a.Size).Append(':').Append(a.Sha256).Append('\n');
            foreach(var f in pin.Files.OrderBy(x=>x.Name,StringComparer.Ordinal)) value.Append(f.Name).Append(':').Append(f.Size).Append(':').Append(f.Sha256).Append('\n');
            using(var hash=SHA256.Create()) return BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(value.ToString()))).Replace("-","").ToLowerInvariant();
        }
        static bool Census(string directory,CudaRuntimePin pin)
        {
            if(!ModelDownload.InspectPath(directory,true)) return false;
            var expected=new HashSet<string>(pin.Files.Select(f=>f.Name),StringComparer.Ordinal);
            foreach(var path in Directory.GetFileSystemEntries(directory)) {
                if(!expected.Remove(Path.GetFileName(path))) throw new InvalidDataException("The CUDA runtime contains unexpected files. Existing files were kept; use CPU mode or a separate application-data folder.");
                ModelDownload.InspectPath(path,false);
            }
            return expected.Count==0;
        }
        static async Task<bool> Matches(string directory,CudaRuntimePin pin,CancellationToken token)
        {
            if(!Census(directory,pin)) return false;
            foreach(var file in pin.Files)
                if(!await ModelDownload.MatchesAsync(Path.Combine(directory,file.Name),file.Size,file.Sha256,token)) return false;
            return true;
        }
        static bool ArchiveCensus(string directory,string name)
        {
            if(!ModelDownload.InspectPath(directory,true)) return false;
            foreach(string path in Directory.GetFileSystemEntries(directory)) {
                if(Path.GetFileName(path)!=name) throw new InvalidDataException("Unexpected CUDA archive cache files were kept.");
                ModelDownload.InspectPath(path,false);
            }
            return true;
        }
        static void DiscardStage(string directory,IEnumerable<string> names)
        {
            if(!Directory.Exists(directory)) return;
            ModelDownload.InspectPath(directory,true);
            var allowed=new HashSet<string>(names,StringComparer.Ordinal);
            var paths=Directory.GetFileSystemEntries(directory);
            foreach(string path in paths) {
                if(!allowed.Contains(Path.GetFileName(path))) throw new InvalidDataException("Unexpected staging files were preserved.");
                ModelDownload.InspectPath(path,false);
            }
            foreach(string path in paths) File.Delete(path);
            Directory.Delete(directory);
        }
        public static Task<string> EnsureAsync(CudaRuntimePin pin,string state,IProgress<double> progress,CancellationToken token)
        { return EnsureAsync(pin,state,progress,token,ModelDownload.EnsureAsync); }
        internal static async Task<string> EnsureAsync(CudaRuntimePin pin,string state,IProgress<double> progress,CancellationToken token,
            Func<ModelPin,string,IProgress<double>,CancellationToken,Task<string>> acquire)
        {
            ValidatePin(pin);token.ThrowIfCancellationRequested();
            if(acquire==null) throw new ArgumentNullException("acquire");
            ModelDownload.InspectPath(state,true);
            string runtimes=Path.Combine(state,"cuda-runtimes");string downloads=Path.Combine(state,"cuda-archives");
            ModelDownload.InspectPath(runtimes,true);ModelDownload.InspectPath(downloads,true);
            string target=Path.Combine(runtimes,pin.ReleaseTag+"-"+pin.Flavor+"-"+Key(pin));
            if(await Matches(target,pin,token)) return target;
            var archives=new List<string>();
            for(int i=0;i<pin.Archives.Length;i++) {
                token.ThrowIfCancellationRequested();var archive=pin.Archives[i];
                string dir=Path.Combine(downloads,archive.Sha256);ModelDownload.InspectPath(dir,true);
                ArchiveCensus(dir,archive.File);
                string pathExpected=Path.Combine(dir,archive.File);
                if(!await ModelDownload.MatchesAsync(pathExpected,archive.Size,archive.Sha256,token)) {
                    int index=i;IProgress<double> part=progress==null?null:new InlineProgress(x=>progress.Report((index+Math.Max(0,Math.Min(1,x)))/pin.Archives.Length));
                    Directory.CreateDirectory(downloads);ModelDownload.InspectPath(downloads,true);
                    string stageArchive=Path.Combine(downloads,"stage-"+Guid.NewGuid().ToString("N"));
                    try {
                        string stagedPath=Path.Combine(stageArchive,archive.File);
                        string actual=await acquire(archive,stageArchive,part,token);
                        if(!String.Equals(Path.GetFullPath(actual),Path.GetFullPath(stagedPath),StringComparison.OrdinalIgnoreCase))
                            throw new InvalidDataException("CUDA acquisition returned an unexpected path.");
                        ArchiveCensus(stageArchive,archive.File);
                        if(!await ModelDownload.MatchesAsync(stagedPath,archive.Size,archive.Sha256,token)) throw new InvalidDataException("The CUDA archive failed size or SHA-256 verification.");
                        token.ThrowIfCancellationRequested();
                        bool exists=ArchiveCensus(dir,archive.File);
                        string previous=dir+".previous-"+Guid.NewGuid().ToString("N");
                        if(exists) Directory.Move(dir,previous);
                        try {token.ThrowIfCancellationRequested();Directory.Move(stageArchive,dir);} catch {if(exists) Directory.Move(previous,dir);throw;}
                    } finally {DiscardStage(stageArchive,new[]{archive.File});}
                }
                if(!await ModelDownload.MatchesAsync(pathExpected,archive.Size,archive.Sha256,token)) throw new InvalidDataException("The CUDA archive failed size or SHA-256 verification.");
                archives.Add(pathExpected);
            }
            Directory.CreateDirectory(runtimes);ModelDownload.InspectPath(runtimes,true);
            string stage=Path.Combine(runtimes,"stage-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(stage);
            try {
                var seen=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                var census=pin.Files.ToDictionary(f=>f.Name,StringComparer.Ordinal);
                foreach(string archive in archives) {
                    using(var zip=ZipFile.OpenRead(archive)) {
                        foreach(var entry in zip.Entries) {
                            token.ThrowIfCancellationRequested();Name(entry.FullName);
                            int kind=(entry.ExternalAttributes>>16)&0xf000;
                            CudaFilePin file;
                            if((kind!=0 && kind!=0x8000) || (entry.ExternalAttributes&0x410)!=0 || !seen.Add(entry.FullName) ||
                                !census.TryGetValue(entry.FullName,out file) || entry.Length!=file.Size)
                                throw new InvalidDataException("The CUDA ZIP file census or entry type is invalid.");
                            using(var input=entry.Open())
                            using(var output=new FileStream(Path.Combine(stage,file.Name),FileMode.CreateNew,FileAccess.Write,FileShare.None,65536,true)) {
                                long remaining=file.Size;var buffer=new byte[65536];int read;
                                while((read=await input.ReadAsync(buffer,0,(int)Math.Min(buffer.Length,remaining+1),token))>0) {
                                    if(read>remaining) throw new InvalidDataException("A CUDA ZIP entry exceeded its declared size.");
                                    await output.WriteAsync(buffer,0,read,token);remaining-=read;
                                }
                                if(remaining!=0) throw new InvalidDataException("A CUDA ZIP entry is incomplete.");
                            }
                        }
                    }
                }
                if(!await Matches(stage,pin,token)) throw new InvalidDataException("The extracted CUDA runtime failed verification.");
                token.ThrowIfCancellationRequested();
                bool exists=ModelDownload.InspectPath(target,true);if(exists) Census(target,pin);
                string previous=target+".previous-"+Guid.NewGuid().ToString("N");
                if(exists) Directory.Move(target,previous);
                try {token.ThrowIfCancellationRequested();Directory.Move(stage,target);} catch {if(exists) Directory.Move(previous,target);throw;}
                return target;
            } finally {
                // Only our new staging directory is disposable. Old and
                // unexpected cache trees are never recursively deleted.
                DiscardStage(stage,pin.Files.Select(f=>f.Name));
            }
        }
        sealed class InlineProgress:IProgress<double> {readonly Action<double> report;public InlineProgress(Action<double> report){this.report=report;}public void Report(double value){report(value);} }
    }
}
