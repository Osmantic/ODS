// SPDX-License-Identifier: Apache-2.0
// Exercise the production-built Portal inside the actual Windows WebView2 control.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading.Tasks;
using System.Threading;
using System.Net;
using System.Net.Sockets;
using System.Windows;
using System.Windows.Controls;
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
        await Until(() => Task.FromResult(View(window).CoreWebView2 != null), 30000, "WebView2 did not initialize.");
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
            Check(await Has(window, "Windows native text chat"), "production Portal assets render inside the real Windows WebView2");
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
