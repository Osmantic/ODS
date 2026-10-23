// SPDX-License-Identifier: Apache-2.0
// Exercise the production-built Portal inside the actual Windows WebView2 control.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.ExceptionServices;
using System.Threading.Tasks;
using System.Threading;
using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.Wpf;
using OdsDesktop;

static class PortalUiTests
{
    static int checks;
    static void Check(bool condition, string message)
    { if (!condition) throw new Exception(message); checks++; Console.WriteLine("PASS " + message); }
    static async Task Until(Func<Task<bool>> predicate, int milliseconds, string message)
    {
        var clock = Stopwatch.StartNew();
        while (clock.ElapsedMilliseconds < milliseconds) { if (await predicate()) return; await Task.Delay(50); }
        throw new TimeoutException(message);
    }
    static WebView2 View(PortalWindow window)
    { return (WebView2)typeof(PortalWindow).GetField("view", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(window); }
    static Task<string> Script(PortalWindow window, string script) { return View(window).CoreWebView2.ExecuteScriptAsync(script); }
    static Task<bool> Has(PortalWindow window, string text)
    { return Test(window, "document.body.innerText.includes(" + new System.Web.Script.Serialization.JavaScriptSerializer().Serialize(text) + ")"); }
    static async Task<bool> Test(PortalWindow window, string expression) { return await Script(window, expression) == "true"; }
    static async Task<bool> InspectActive(LocalRuntime runtime)
    {
        try { return await runtime.IsProcessingAsync(CancellationToken.None); }
        catch (OperationCanceledException) { return false; } // Unknown; bounded Until still requires a real active-slot response.
    }
    static async Task Ready(PortalWindow window)
    {
        string browser;
        try { browser = CoreWebView2Environment.GetAvailableBrowserVersionString(); }
        catch (Exception error) { browser = error.ToString(); }
        Console.WriteLine("WEBVIEW available " + browser);
        var view = View(window);
        EventHandler<CoreWebView2InitializationCompletedEventArgs> initialized = (sender, args) =>
            Console.WriteLine("WEBVIEW initialization: " + (args.IsSuccess ? "success" :
                args.InitializationException == null ? "failed without an exception" : args.InitializationException.ToString()));
        view.CoreWebView2InitializationCompleted += initialized;
        try
        {
            await Until(() =>
            {
                // Production replaces the content with its actionable error.
                // Preserve that evidence instead of hiding it behind a timeout.
                var failure = window.Content as TextBlock;
                if (failure != null && failure.Text.StartsWith("Portal could not open.", StringComparison.Ordinal))
                    throw new InvalidOperationException("Production Portal initialization failed: " + failure.Text);
                return Task.FromResult(view.CoreWebView2 != null);
            }, 30000, "WebView2 did not initialize. Available runtime: " + browser);
        }
        finally { view.CoreWebView2InitializationCompleted -= initialized; }
        await Until(() => Test(window, "!!document.querySelector('textarea:not([disabled])')"), 30000, "The existing Portal composer did not become available.");
        Console.WriteLine("WEBVIEW runtime " + View(window).CoreWebView2.Environment.BrowserVersionString);
    }
    static Task<string> Send(PortalWindow window, string message)
    {
        string value = new System.Web.Script.Serialization.JavaScriptSerializer().Serialize(message);
        return Script(window, "(()=>{const input=document.querySelector('textarea');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(input," + value + ");input.dispatchEvent(new Event('input',{bubbles:true}));return true})()");
    }
    static async Task ClickSend(PortalWindow window)
    {
        await Until(() => Test(window, "!!document.querySelector('button[title=\"Send\"]:not([disabled])')"), 10000, "Send button did not accept the draft.");
        await Script(window, "document.querySelector('button[title=\"Send\"]').click()");
    }
    static async Task Fixture(string package, string state)
    {
        var model = new NativePortalTests.Model();
        var window = new PortalWindow(model, package, state, "Native fixture model");
        window.Show();
        try
        {
            await Ready(window);
            Check(await Has(window, "Windows native chat and documents"), "production Portal assets render inside the real Windows WebView2");
            Check(!await Has(window, "Permissions") && !await Has(window, "Workspace"), "native Portal hides unsupported host and workspace controls");
            await Send(window, "Xin chào"); await ClickSend(window);
            await Until(() => Has(window, "Answer: Xin chào"), 15000, "Native Portal did not display the model answer.");
            await Until(() => Test(window, "!document.querySelector('button[title=\"Stop\"]')"), 10000, "First answer did not settle.");
            Check(model.Calls == 1 && model.Last.Last()["content"] == "Xin chào", "existing Portal send path reaches the private native adapter and returns UTF-8 text");
            await Send(window, "second turn"); await ClickSend(window);
            await Until(() => Has(window, "Answer: second turn"), 15000, "Second turn did not complete.");
            Check(model.Last.Count == 3 && model.Last[1]["content"] == "Answer: Xin chào", "native Portal preserves the real producer's multi-turn history contract");
            await Until(() => Test(window, "!document.querySelector('button[title=\"Stop\"]')"), 10000, "Second answer did not settle.");
        }
        finally { window.Close(); }
        window = new PortalWindow(model, package, state, "Native fixture model"); window.Show();
        try
        {
            await Ready(window);
            Check(await Has(window, "Answer: second turn") && model.Calls == 2, "closing and reopening the native Portal restores saved history without replaying inference");
            await Script(window, "document.querySelector('.pixel-conversation-navigation > button.pixel-nav-item').click()");
            await Until(() => Test(window, "document.querySelectorAll('[data-pixel-message-index]').length===0"), 10000, "New chat did not clear the active view.");
            Check(model.Calls == 2, "native Portal New chat does not submit or erase earlier model work");
            await Script(window, "document.querySelector('.conversation-link').click()");
            await Until(() => Has(window, "Answer: second turn"), 10000, "Saved conversation could not be selected.");
            Check(model.Calls == 2, "the existing Portal conversation navigation restores the chosen conversation without replay");
            model.Hold = true;
            await Send(window, "stop this turn"); await ClickSend(window);
            await Until(() => Task.FromResult(model.Calls == 3), 10000, "The cancellable turn did not start.");
            await Script(window, "document.querySelector('button[title=\"Stop\"]').click()");
            await Until(() => Has(window, "Stopped by you."), 15000, "The exact Stop acknowledgement did not reach the Portal.");
            Check(model.Inspections == 1, "real Portal Stop waits for native cancellation and slot inspection");
            model.Hold = false;
            await Send(window, "after stop"); await ClickSend(window);
            await Until(() => Has(window, "Answer: after stop"), 15000, "Native Portal did not recover after Stop.");
            Check(model.Calls == 4, "the same native Portal conversation can continue after acknowledged Stop");
            string before = View(window).CoreWebView2.Source;
            View(window).CoreWebView2.Navigate("https://outside.invalid/"); await Task.Delay(200);
            Check(View(window).CoreWebView2.Source == before, "external navigation cannot replace the trusted Portal document");
            await Script(window, "window.__outsideBlocked=null;window.__nativeCsp=false;window.addEventListener('securitypolicyviolation',event=>{if(event.blockedURI.includes('outside.invalid'))window.__nativeCsp=true});fetch('https://outside.invalid/private').then(()=>window.__outsideBlocked=false,()=>window.__outsideBlocked=true)");
            await Until(() => Test(window, "window.__outsideBlocked===true && window.__nativeCsp===true"), 10000, "External fetch did not produce the expected CSP refusal.");
            Check(true, "native CSP blocks external fetches from the Portal document");
        }
        finally { window.Close(); }
    }
    static T Find<T>(DependencyObject root, Func<T, bool> test) where T : DependencyObject
    {
        var current = root as T; if (current != null && test(current)) return current;
        foreach (var item in LogicalTreeHelper.GetChildren(root))
        { var child = item as DependencyObject; if (child != null) { var found = Find(child, test); if (found != null) return found; } }
        return null;
    }
    sealed class DocumentModel : INativeDocumentModel
    {
        public readonly NativeDocument Document = NativeDocument.Prepare("# Windows document fixture\n\nExact UTF-8 bytes: caf\u00e9, ti\u1ebfng Vi\u1ec7t, \ud83d\udcc4.\n");
        public bool IsRunning { get; private set; }
        public int Chats, Documents;
        public List<Dictionary<string, string>> Last;
        public DocumentModel() { IsRunning = true; }
        public Task<string> CompleteAsync(List<Dictionary<string, string>> messages, CancellationToken token)
        { token.ThrowIfCancellationRequested(); Chats++; return Task.FromResult("Answer: " + messages.Last()["content"]); }
        public Task<NativeDocumentReply> CreateDocumentAsync(List<Dictionary<string, string>> messages, CancellationToken token)
        {
            token.ThrowIfCancellationRequested(); Documents++; Last = messages;
            return Task.FromResult(new NativeDocumentReply("Your Markdown document is ready. Use the download card.", Document));
        }
        public Task VerifyIdleAsync() { return Task.FromResult(0); }
        public void Dispose() { IsRunning = false; }
    }
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 8 * 1024 * 1024 };
    static string Hash(byte[] bytes)
    { using (var hash = SHA256.Create()) return BitConverter.ToString(hash.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant(); }
    static async Task<Dictionary<string, object>> ObjectScript(PortalWindow window, string expression)
    { return (Dictionary<string, object>)Json.DeserializeObject(await Script(window, expression)); }
    static string Canonical(object value)
    {
        var map = value as Dictionary<string, object>;
        if (map != null) return "{" + String.Join(",", map.Keys.OrderBy(key => key, StringComparer.Ordinal)
            .Select(key => Json.Serialize(key) + ":" + Canonical(map[key]))) + "}";
        var rows = value as object[];
        return rows == null ? Json.Serialize(value) : "[" + String.Join(",", rows.Select(Canonical)) + "]";
    }
    static async Task DocumentReady(PortalWindow window, int milliseconds)
    {
        await Until(() => Test(window, "!!document.querySelector('section[aria-label=\"Delivered files\"] button[aria-label=\"Download document.md\"]') && !document.querySelector('button[title=\"Stop\"]')"),
            milliseconds, "The real Portal did not render a completed delivered-document card.");
    }
    static async Task SelectDocument(PortalWindow window)
    {
        await Script(window, "[...document.querySelectorAll('input[name=\"native-response-mode\"]')].at(1).click()");
        Check(await Test(window, "document.querySelectorAll('input[name=\"native-response-mode\"]')[1].checked"), "Create document is an explicit composer choice in the actual WebView");
    }
    static async Task TrustedDownloadClick(PortalWindow window, bool markdown)
    {
        string control = markdown ? "document.querySelector('section[aria-label=\"Delivered files\"] button[aria-label=\"Download document.md\"]')"
            : "[...document.querySelectorAll('[role=\"menuitem\"]')].find(button=>button.textContent==='Export conversation')";
        var point = await ObjectScript(window, "(()=>{const button=" + control + ";button.scrollIntoView({block:'center'});const box=button.getBoundingClientRect();return {x:box.left+box.width/2,y:box.top+box.height/2}})()");
        // Script .click() has no browser user activation and can suppress a
        // second download. Send input only to this retained WebView instance's
        // actual control coordinates; never use global mouse/keyboard input.
        var core = View(window).CoreWebView2;
        await core.CallDevToolsProtocolMethodAsync("Input.dispatchMouseEvent", Json.Serialize(new { type = "mousePressed", x = point["x"], y = point["y"], button = "left", buttons = 1, clickCount = 1 }));
        await core.CallDevToolsProtocolMethodAsync("Input.dispatchMouseEvent", Json.Serialize(new { type = "mouseReleased", x = point["x"], y = point["y"], button = "left", buttons = 0, clickCount = 1 }));
    }
    static async Task ChooseDownload(PortalWindow window, string directory, string filename, bool accept, bool markdown, int holdMilliseconds = 0)
    {
        string target = Path.Combine(directory, filename);
        var core = View(window).CoreWebView2;
        CoreWebView2DownloadOperation operation = null;
        string result = null, mime = null, uri = null;
        bool? cancelled = null;
        EventHandler<CoreWebView2DownloadStartingEventArgs> observed = (sender, args) =>
        {
            operation = args.DownloadOperation; cancelled = args.Cancel;
            result = args.ResultFilePath; mime = operation.MimeType; uri = operation.Uri;
            Console.WriteLine("DOWNLOAD mime=" + mime + " suggested=" + Path.GetFileName(result) + " bytes=" + operation.TotalBytesToReceive);
        };
        EventHandler<CoreWebView2NavigationStartingEventArgs> navigation = (sender, args) =>
        { Console.WriteLine("DOWNLOAD navigation uri=" + args.Uri + " cancelled=" + args.Cancel); };
        EventHandler<CoreWebView2PermissionRequestedEventArgs> permission = (sender, args) =>
        { Console.WriteLine("DOWNLOAD permission kind=" + args.PermissionKind + " user=" + args.IsUserInitiated + " state=" + args.State); };
        core.DownloadStarting += observed; // observes production's choice; never changes args.
        core.NavigationStarting += navigation;
        core.PermissionRequested += permission;
        using (var cancellation = new CancellationTokenSource())
        {
            var choice = DocumentSaveDialogTests.ChooseAsync(new WindowInteropHelper(window).Handle, target, accept,
                cancellation.Token, markdown ? "Save document" : "Save conversation", markdown ? "document.md" : filename, holdMilliseconds);
            Task interaction = null;
            Exception failure = null;
            try
            {
                interaction = Task.WhenAll(TrustedDownloadClick(window, markdown), choice);
                // UIA Invoke can block inside a native validation modal. Its
                // worker's polling deadline cannot interrupt that call, so
                // bound the whole interaction from the WPF dispatcher too.
                if (await Task.WhenAny(interaction, Task.Delay(35000)) != interaction)
                    throw new TimeoutException("The owned download click/save-dialog interaction exceeded 35 seconds.");
                await interaction;
                await Until(() => Task.FromResult(cancelled.HasValue), 10000, "Production download event did not finish the owner choice.");
                Check(uri.StartsWith("blob:" + NativePortalSession.Origin + "/", StringComparison.Ordinal) &&
                    mime == (markdown ? "application/octet-stream" : "application/json"), "the real component passes its typed private Blob through the production download boundary");
                if (!accept)
                {
                    await Until(() => Task.FromResult(Find<TextBlock>(window, block => block.Text == "Download cancelled. No file was saved.") != null),
                        10000, "The production picker did not acknowledge owner cancellation.");
                    Check(cancelled == true && !File.Exists(target), "Cancel in the production save dialog authorizes no fixture file write");
                    Check(Find<TextBlock>(window, block => block.Text == "Download cancelled. No file was saved.") != null,
                        "native download status accurately reports owner cancellation");
                }
                else
                {
                    try { await Until(() => Task.FromResult(operation.State == CoreWebView2DownloadState.Completed), 15000, "The accepted production download did not complete."); }
                    catch (TimeoutException)
                    {
                        Console.WriteLine("DOWNLOAD timeout state=" + operation.State + " reason=" + operation.InterruptReason + " resumable=" + operation.CanResume +
                            " path=" + operation.ResultFilePath + " targetExists=" + File.Exists(target));
                        var status = Find<TextBlock>(window, block => block.Text.StartsWith("Saving ") || block.Text.StartsWith("The download ") || block.Text.StartsWith("File saved") || block.Text.StartsWith("Download cancelled"));
                        Console.WriteLine("DOWNLOAD native status=" + (status == null ? "absent" : status.Text));
                        throw;
                    }
                    Check(Path.GetFullPath(operation.ResultFilePath) == Path.GetFullPath(target) && File.Exists(target),
                        "Save in the production picker writes only the owner's chosen private fixture path");
                }
            }
            catch (Exception error) { failure = error; }
            finally
            {
                cancellation.Cancel(); core.DownloadStarting -= observed; core.NavigationStarting -= navigation; core.PermissionRequested -= permission;
            }
            // The shipped Framework compiler is C# 5: cleanup awaits must sit
            // outside catch/finally. Preserve the original test failure.
            if (failure != null)
            {
                Task pending = interaction ?? choice;
                await Task.WhenAny(pending, Task.Delay(1000));
                // Never wait indefinitely for Invoke during failure cleanup:
                // rethrow so the outer finally closes this exact owner window
                // and releases its dialog. Observe any later worker failure.
                var observedFailure = pending.ContinueWith(completed => GC.KeepAlive(completed.Exception), CancellationToken.None,
                    TaskContinuationOptions.OnlyOnFaulted | TaskContinuationOptions.ExecuteSynchronously, TaskScheduler.Default);
                GC.KeepAlive(observedFailure);
                ExceptionDispatchInfo.Capture(failure).Throw();
            }
        }
    }
    static async Task SaveDocumentChecks(PortalWindow window, string directory, NativeDocument document)
    {
        Directory.CreateDirectory(directory);
        int requests = 0;
        EventHandler<CoreWebView2WebResourceRequestedEventArgs> observed = (sender, args) =>
        { if (args.Request.Uri.StartsWith(NativePortalSession.Origin + "/pixel-preview/", StringComparison.Ordinal)) requests++; };
        View(window).CoreWebView2.WebResourceRequested += observed;
        try
        {
            await ChooseDownload(window, directory, "cancelled.md", false, true);
            Check(Directory.GetFiles(directory).Length == 0 && requests == 1,
                "the verified card fetched the host artifact but cancelling leaves the private download directory empty");
            await ChooseDownload(window, directory, "document.md", true, true, 2000);
            byte[] saved = File.ReadAllBytes(Path.Combine(directory, "document.md"));
            Check(requests == 2 && saved.SequenceEqual(document.ReadBytes()) && Hash(saved) == document.FileSha256,
                "the existing verified card downloads exact host-produced Markdown bytes and SHA-256 through the real picker");
        }
        finally { View(window).CoreWebView2.WebResourceRequested -= observed; }
    }
    static async Task OpenExport(PortalWindow window)
    {
        await Script(window, "document.querySelector('.conversation-link[aria-current=\"page\"]').closest('.conversation-row').dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true}))");
        await Until(() => Test(window, "[...document.querySelectorAll('[role=\"menuitem\"]')].some(button=>button.textContent==='Export conversation')"), 5000, "The real conversation export action did not open.");
    }
    static async Task ExportChecks(PortalWindow window, string directory, bool large)
    {
        Directory.CreateDirectory(directory);
        // ExecuteScript's object marshalling can reorder keys. Preserve the
        // saved producer input's raw JSON for byte-for-byte export comparison.
        string conversationJson = (string)Json.DeserializeObject(await Script(window, "localStorage.getItem('ods.pixel.chat.v1')"));
        var conversation = (Dictionary<string, object>)Json.DeserializeObject(conversationJson);
        string filename = "ods-portal-" + (string)conversation["chatId"] + ".json";
        DateTime started = DateTime.UtcNow;
        await OpenExport(window);
        await ChooseDownload(window, directory, filename, false, false);
        Check(Directory.GetFiles(directory).Length == 0, "cancelling a real JSON conversation export writes no fixture file");
        await OpenExport(window);
        await ChooseDownload(window, directory, filename, true, false, large ? 0 : 2000);
        byte[] saved = File.ReadAllBytes(Path.Combine(directory, filename));
        var archive = (Dictionary<string, object>)Json.DeserializeObject(new UTF8Encoding(false, true).GetString(saved));
        DateTime exported;
        // Independently reconstruct the existing export contract from the
        // saved input plus its bounded actual timestamp. Do not override Blob,
        // URL, anchor, Date or the production component to observe its bytes.
        string expected = (string)Json.DeserializeObject(await Script(window, "JSON.stringify({schemaVersion:1,kind:'ods-pixel-conversation',exportedAt:" +
            Json.Serialize(archive["exportedAt"]) + ",conversation:" + conversationJson + "},null,2)"));
        byte[] expectedBytes = new UTF8Encoding(false, true).GetBytes(expected);
        if (!saved.SequenceEqual(expectedBytes))
        {
            File.WriteAllBytes(Path.Combine(directory, "expected-export.json"), expectedBytes);
            int mismatch = Enumerable.Range(0, Math.Min(saved.Length, expectedBytes.Length)).FirstOrDefault(index => saved[index] != expectedBytes[index]);
            Console.WriteLine("EXPORT expected bytes=" + expectedBytes.Length + " actual=" + saved.Length + " first difference=" + mismatch +
                " conversationEqual=" + (Canonical(archive["conversation"]) == Canonical(conversation)));
        }
        Check(saved.SequenceEqual(expectedBytes) && Hash(saved) == Hash(expectedBytes) &&
            (large ? saved.Length > 65536 : saved.Length < 65536),
            (large ? "large >64 KiB" : "small") + " JSON export preserves the real component's exact Blob bytes, hash and .json name");
        Check(Equals(archive["schemaVersion"], 1) && Equals(archive["kind"], "ods-pixel-conversation") &&
            DateTime.TryParse((string)archive["exportedAt"], out exported) && exported.ToUniversalTime() >= started && exported.ToUniversalTime() <= DateTime.UtcNow &&
            Canonical(archive["conversation"]) == Canonical(conversation),
            "the exported archive contains the complete unchanged saved conversation and existing schema");
    }
    static async Task DocumentFixture(string package, string state)
    {
        Check(String.Equals(Path.GetFullPath(Environment.CurrentDirectory).TrimEnd('\\'), Path.GetFullPath(package).TrimEnd('\\'), StringComparison.OrdinalIgnoreCase),
            "the graphical fixture runs from its isolated package directory, keeping picker defaults outside the source checkout");
        var model = new DocumentModel();
        var window = new PortalWindow(model, package, state, "Native document fixture"); window.Show();
        try
        {
            await Ready(window);
            Check(await Has(window, "Windows native chat and documents"), "native document capability is visible in the production Portal");
            await Send(window, "ordinary fixture chat"); await ClickSend(window);
            await Until(() => Has(window, "Answer: ordinary fixture chat"), 15000, "Ordinary chat did not complete.");
            await Until(() => Test(window, "!document.querySelector('button[title=\"Stop\"]')"), 10000, "Ordinary chat did not settle.");
            Check(model.Chats == 1 && model.Documents == 0, "default Chat mode never admits document generation");
            await SelectDocument(window);
            await Send(window, "Create a short Markdown document for this owner."); await ClickSend(window);
            await DocumentReady(window, 15000);
            Check(model.Documents == 1 && model.Chats == 1 && model.Last.Count == 3,
                "actual composer opt-in reaches INativeDocumentModel with the saved preceding turn");
            var store = new PortalState(Path.Combine(state, "portal", "results"));
            Check(store.ReadDocument(model.Document.SiteId).ReadBytes().SequenceEqual(model.Document.ReadBytes()),
                "the host's completed receipt authorizes the exact artifact rendered by the real Portal card");
            await SaveDocumentChecks(window, Path.Combine(state, "document-downloads"), model.Document);
            await ExportChecks(window, Path.Combine(state, "small-export"), false);
            await Script(window, "document.querySelectorAll('input[name=\"native-response-mode\"]')[0].click()");
            for (int turn = 0; turn < 3; turn++)
            {
                string prompt = "Large export turn " + turn + ": " + new string('x', 12000);
                await Send(window, prompt); await ClickSend(window);
                int expected = turn + 2;
                await Until(() => Task.FromResult(model.Chats == expected), 10000, "Large export request did not reach the fixture model.");
                await Until(() => Test(window, "!document.querySelector('button[title=\"Stop\"]') && document.querySelector('textarea').value===''"),
                    15000, "Large export fixture turn did not settle.");
            }
            Check(model.Chats == 4 && model.Documents == 1, "large export history is produced through ordinary real Portal sends");
            await ExportChecks(window, Path.Combine(state, "large-export"), true);
        }
        finally { window.Close(); }
        var restored = new DocumentModel();
        window = new PortalWindow(restored, package, state, "Native document fixture"); window.Show();
        try
        {
            await Ready(window); await DocumentReady(window, 15000);
            Check(restored.Chats == 0 && restored.Documents == 0 &&
                await Test(window, "document.querySelectorAll('input[name=\"native-response-mode\"]')[0].checked"),
                "reopening restores the delivered card and ordinary Chat mode without replaying model work");
        }
        finally { window.Close(); }
    }
    static async Task DocumentLive(string package, string state, string screenshot)
    {
        var setup = new ChatWindow(package, state, Bundle.Read(package)); setup.Show();
        try
        {
            var start = Find<Button>(setup, button => ((string)button.Content).Contains("start") || (string)button.Content == "Start local AI");
            start.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            await Until(() => Task.FromResult(Application.Current.Windows.OfType<PortalWindow>().Any()), 300000, "Packaged startup did not hand off to the document Portal.");
            var window = Application.Current.Windows.OfType<PortalWindow>().Single();
            var runtime = (LocalRuntime)typeof(ChatWindow).GetField("runtime", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(setup);
            try
            {
                await Ready(window);
                await Script(window, "document.querySelector('.pixel-conversation-navigation > button.pixel-nav-item').click()");
                await Until(() => Test(window, "document.querySelectorAll('[data-pixel-message-index]').length===0"), 10000, "Live document probe could not open a fresh conversation.");
                await SelectDocument(window);
                await Send(window, "Create a short Markdown document titled Windows native document. Include three practical bullet points about keeping local notes. Use the create_markdown tool, then briefly tell me to download the document.");
                await ClickSend(window); await DocumentReady(window, 300000);
                var artifact = await ObjectScript(window, "JSON.parse(localStorage.getItem('ods.pixel.chat.v1')).messages.filter(row=>row.artifacts?.length).at(-1).artifacts[0]");
                var store = new PortalState(Path.Combine(state, "portal", "results"));
                var document = store.ReadDocument((string)artifact["siteId"]);
                Check(document != null && document.ReadBytes().Length > 0 && document.FileSha256 == (string)((Dictionary<string, object>)artifact["file"])["sha256"],
                    "the real pinned model's tool outcome is atomically retained and exposed through the production delivered-file contract");
                Console.WriteLine("LIVE DOCUMENT bytes=" + document.ReadBytes().Length + " sha256=" + document.FileSha256);
                await SaveDocumentChecks(window, Path.Combine(state, "live-document-downloads-" + Guid.NewGuid().ToString("N")), document);
                Check(!await runtime.IsProcessingAsync(CancellationToken.None), "real document tool and final-answer inference finish with the owned slot idle");
                if (!String.IsNullOrEmpty(screenshot))
                    using (var image = File.Create(screenshot)) await View(window).CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, image);
                using (var child = Process.GetProcessById(runtime.ProcessId))
                {
                    var retained = child.Handle; window.Close();
                    Check(child.HasExited || child.WaitForExit(5000), "closing the real document Portal stops its exact owned model process");
                }
            }
            finally { if (window.IsVisible) window.Close(); }
        }
        finally { setup.Close(); }
    }
    static async Task VerifySaved(string package, string state)
    {
        var model = new NativePortalTests.Model();
        var window = new PortalWindow(model, package, state, "Native fixture model"); window.Show();
        try
        {
            await Ready(window);
            Check(await Has(window, "Answer: after stop"), "actual Portal history remains readable after installer lifecycle operations");
            Check(model.Calls == 0, "restoring saved Portal history after uninstall does not replay inference");
        }
        finally { window.Close(); }
    }
    static async Task Live(string package, string state, string screenshot)
    {
        var setup = new ChatWindow(package, state, Bundle.Read(package)); setup.Show();
        try
        {
            var start = Find<Button>(setup, button => ((string)button.Content).Contains("start") || (string)button.Content == "Start local AI");
            start.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            var clock = Stopwatch.StartNew();
            await Until(() => Task.FromResult(Application.Current.Windows.OfType<PortalWindow>().Any()), 300000, "Packaged startup did not hand off to Portal.");
            var window = Application.Current.Windows.OfType<PortalWindow>().Single();
            var runtime = (LocalRuntime)typeof(ChatWindow).GetField("runtime", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(setup);
            await Ready(window);
            await Script(window, "document.querySelector('.pixel-conversation-navigation > button.pixel-nav-item').click()");
            await Until(() => Test(window, "document.querySelectorAll('[data-pixel-message-index]').length===0"), 10000, "Live probe could not open a fresh conversation.");
            Console.WriteLine("LIVE Portal ready in " + clock.Elapsed.TotalSeconds.ToString("F1") + " seconds");
            await Send(window, "What is 2+2? Answer with the number only."); await ClickSend(window);
            await Until(() => Test(window, "[...document.querySelectorAll('[data-pixel-message-index] .portal-streaming-text')].at(-1)?.innerText.trim() === '4'"), 180000, "Real model did not complete the Portal math probe.");
            await Until(() => Test(window, "!document.querySelector('button[title=\"Stop\"]')"), 10000, "Real turn did not settle.");
            Check(true, "actual packaged setup opens the existing Portal and completes a real local-model turn");
            await Send(window, "Hãy chào tôi bằng một câu tiếng Việt."); await ClickSend(window);
            await Until(() => Test(window, "!document.querySelector('button[title=\"Stop\"]') && document.querySelector('textarea').value === '' && [...document.querySelectorAll('[data-pixel-message-index] .portal-streaming-text')].at(-1)?.getAttribute('aria-busy') === 'false'"), 180000, "Vietnamese Portal turn did not finish rendering.");
            Console.WriteLine("LIVE Portal transcript: " + await Script(window, "[...document.querySelectorAll('[data-pixel-message-index]')].map(row=>row.innerText).join('\\n')"));
            Check(true, "real local-model Portal conversation completes a second UTF-8 turn");
            await Send(window, "Write a very long story with at least 100 numbered paragraphs about the ocean. Keep writing until the response limit."); await ClickSend(window);
            await Until(() => InspectActive(runtime), 30000, "The real cancellable inference did not become active.");
            await Script(window, "document.querySelector('button[title=\"Stop\"]').click()");
            await Until(() => Test(window, "[...document.querySelectorAll('[data-pixel-message-index]')].at(-1)?.innerText.includes('Stopped by you.') === true && !document.querySelector('button[title=\"Stop\"]')"), 30000, "Real model Stop did not reach the exact current Portal turn.");
            Check(runtime.IsRunning, "real model Stop inspects an idle owned slot and keeps the model available");
            await Send(window, "What is 2+2? Answer with the number only."); await ClickSend(window);
            await Until(() => Test(window, "[...document.querySelectorAll('[data-pixel-message-index] .portal-streaming-text')].at(-1)?.innerText.trim() === '4'"), 180000, "Real model did not recover after Stop.");
            Check(true, "real Portal conversation continues after stopping actual inference");
            var longHistory = new List<Dictionary<string, string>>();
            for (int turn = 0; turn < 12; turn++)
            {
                longHistory.Add(new Dictionary<string, string> { { "role", "user" }, { "content", String.Concat(Enumerable.Repeat("Earlier question. ", 100)) } });
                longHistory.Add(new Dictionary<string, string> { { "role", "assistant" }, { "content", String.Concat(Enumerable.Repeat("Earlier answer. ", 100)) } });
            }
            longHistory.Add(new Dictionary<string, string> { { "role", "user" }, { "content", "What is 2+2? Answer with the number only." } });
            string longAnswer = await runtime.CompleteAsync(longHistory, CancellationToken.None);
            Check(longAnswer.Trim() == "4" && longHistory.Count == 25 && longHistory[0]["content"].Length == 1800,
                "real pinned template/tokenizer fits an over-context conversation and answers without mutating its full history");
            bool oversizedRefused = false;
            try { await runtime.CompleteAsync(new List<Dictionary<string, string>> { new Dictionary<string, string> {
                { "role", "user" }, { "content", String.Concat(Enumerable.Repeat("x ", 5000)) } } }, CancellationToken.None); }
            catch (NativeContextLimitException) { oversizedRefused = true; }
            Check(oversizedRefused && !await runtime.IsProcessingAsync(CancellationToken.None),
                "real tokenizer refuses an oversized latest message before inference and leaves the slot idle");
            string afterRefusal = await runtime.CompleteAsync(new List<Dictionary<string, string>> { new Dictionary<string, string> {
                { "role", "user" }, { "content", "What is 2+2? Answer with the number only." } } }, CancellationToken.None);
            Check(afterRefusal.Trim() == "4", "real model accepts a normal next request after context refusal");
            if (!String.IsNullOrEmpty(screenshot))
                using (var image = File.Create(screenshot)) await View(window).CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, image);
            var foreign = new TcpListener(IPAddress.Loopback, 0); foreign.Start();
            try
            {
                using (var child = Process.GetProcessById(runtime.ProcessId))
                {
                    var retained = child.Handle; window.Close();
                    Check(child.HasExited || child.WaitForExit(5000), "closing the actual Portal stops its exact owned model process");
                    Check(foreign.Server.IsBound, "native Portal shutdown preserves an unrelated listener");
                }
            }
            finally { foreign.Stop(); }
        }
        finally { setup.Close(); }
    }
    [STAThread]
    static int Main(string[] args)
    {
        int result = 1;
        var app = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
        app.Startup += async (sender, eventArgs) =>
        {
            try
            {
                string package = AppDomain.CurrentDomain.BaseDirectory;
                if (args.Length > 0 && args[0] == "--live") await Live(package, args[1], args.Length > 2 ? args[2] : "");
                else if (args.Length > 0 && args[0] == "--document-live") await DocumentLive(package, args[1], args.Length > 2 ? args[2] : "");
                else if (args.Length > 0 && args[0] == "--document-fixture") await DocumentFixture(package, args.Length > 1 ? args[1] : Path.Combine(package, "qa-document-state"));
                else if (args.Length > 0 && args[0] == "--saved-state") await Fixture(package, args[1]);
                else if (args.Length > 0 && args[0] == "--verify-state") await VerifySaved(package, args[1]);
                else await Fixture(package, Path.Combine(package, "qa-state"));
                Console.WriteLine("PASS " + checks + " Windows Portal WebView checks"); result = 0;
            }
            catch (Exception error) { Console.Error.WriteLine(error); }
            finally { app.Shutdown(); }
        };
        app.Run(); return result;
    }
}
