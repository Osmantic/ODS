// SPDX-License-Identifier: Apache-2.0
// Drive the actual WPF controls and dispatcher, without desktop automation.
using System;
using System.Collections;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Documents;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using OdsDesktop;

static class WindowTests
{
    static int checks;
    static void Check(bool condition, string message)
    { if (!condition) throw new Exception(message); checks++; Console.WriteLine("PASS " + message); }
    static T Find<T>(DependencyObject parent, Func<T, bool> predicate) where T : DependencyObject
    {
        var current = parent as T;
        if (current != null && predicate(current)) return current;
        foreach (object item in LogicalTreeHelper.GetChildren(parent))
        {
            var child = item as DependencyObject;
            if (child == null) continue;
            T result = Find(child, predicate); if (result != null) return result;
        }
        return null;
    }
    static Button Button(Window window, string text)
    { return Find<Button>(window, button => (string)button.Content == text); }
    static void Click(Button button) { button.RaiseEvent(new RoutedEventArgs(System.Windows.Controls.Button.ClickEvent)); }
    static void Until(Func<bool> predicate, int milliseconds)
    {
        var clock = Stopwatch.StartNew();
        var frame = new DispatcherFrame();
        var timer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(20) };
        timer.Tick += (sender, args) => { if (predicate() || clock.ElapsedMilliseconds > milliseconds) frame.Continue = false; };
        timer.Start(); Dispatcher.PushFrame(frame); timer.Stop();
        if (!predicate()) throw new TimeoutException("WPF operation did not finish in " + milliseconds + " ms.");
    }
    static ChatWindow Open(string package, string state, Bundle bundle)
    {
        if (Application.Current == null) new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
        SynchronizationContext.SetSynchronizationContext(new DispatcherSynchronizationContext());
        var window = new ChatWindow(package, state, bundle) { ShowInTaskbar = false, Left = -10000, Top = -10000,
            WindowStartupLocation = WindowStartupLocation.Manual };
        window.Show(); return window;
    }
    public static int Run(string root, Func<string, string, Bundle> makeBundle)
    {
        string package = Path.Combine(root, "window-package"), state = Path.Combine(root, "window-state");
        Bundle bundle = makeBundle(package, "fixture");
        string native = Path.Combine(state, "runtimes", "fixture"); Directory.CreateDirectory(native);
        File.Copy(Path.Combine(package, "runtime", "llama-server.exe"), Path.Combine(native, "llama-server.exe"));
        Directory.CreateDirectory(Path.Combine(package, "models"));
        Directory.CreateDirectory(Path.Combine(state, "models"));
        File.WriteAllText(Path.Combine(package, "models", bundle.Model.File), "damaged bundled seed");
        File.Copy(Path.Combine(package, bundle.Model.File), Path.Combine(state, "models", bundle.Model.File));
        var window = Open(package, state, bundle);
        try
        {
            var input = Find<TextBox>(window, box => true);
            var viewer = Find<FlowDocumentScrollViewer>(window, item => true);
            var start = Button(window, "Start local AI"); var send = Button(window, "Send");
            Check(!send.IsEnabled && !input.IsEnabled, "GUI keeps chat unavailable until the model is ready");
            Click(start); Until(() => send.IsEnabled || start.IsEnabled, 20000);
            Check(send.IsEnabled, "GUI recovers a corrupt bundled model through a valid cache");
            input.Text = "Xin chào"; Click(send); Until(() => input.IsEnabled, 10000);
            string text = new TextRange(viewer.Document.ContentStart, viewer.Document.ContentEnd).Text;
            Check(text.Contains("You") && text.Contains("Answer: Xin chào") && input.Text == "", "GUI sends and displays the completed UTF-8 conversation");
            input.Text = "empty"; Click(send); Until(() => input.IsEnabled, 10000);
            Check(input.Text == "empty" && send.IsEnabled && new TextRange(viewer.Document.ContentStart, viewer.Document.ContentEnd).Text == text,
                "GUI failure keeps the unsent message and earlier conversation ready for retry");
            input.Text = "wait"; Click(send); Click(Button(window, "Cancel"));
            Until(() => start.IsEnabled, 10000);
            Check(input.Text == "wait" && viewer.Document.Blocks.Count == 0 && !send.IsEnabled,
                "GUI Cancel stops the model, clears stale context and keeps the unsent message");
            Click(start); Until(() => send.IsEnabled || start.IsEnabled, 20000);
            Check(send.IsEnabled, "GUI restarts from its verified caches after cancellation");
            input.Text = "again"; Click(send); Until(() => input.IsEnabled, 10000);
            Click(Button(window, "New chat"));
            Check(viewer.Document.Blocks.Count == 0, "GUI New chat clears the visible transcript");
            typeof(ChatWindow).GetMethod("HandleFailure", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(window,
                new object[] { new TargetInvocationException(new TargetInvocationException(new IOException("Saved Portal history needs attention."))), "cancelled", true });
            Check(Find<TextBlock>(window, block => block.Text == "Saved Portal history needs attention.") != null,
                "GUI unwraps nested reflection failures into the actionable Portal error");
            typeof(ChatWindow).GetMethod("HandleFailure", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(window,
                new object[] { new OperationCanceledException(), "cancelled", true });
            Check(Find<TextBlock>(window, block => block.Text.StartsWith("Setup timed out.") && !block.Text.Contains("shorter message")) != null,
                "GUI setup timeout gives download recovery guidance instead of chat advice");
        }
        finally { window.Close(); }
        using (var server = new DesktopTests.DownloadServer(System.Text.Encoding.UTF8.GetBytes("fixture")))
        {
            bundle.Model.Url = server.Url;
            File.WriteAllText(Path.Combine(package, "models", bundle.Model.File), "xxxxxxx");
            File.WriteAllText(Path.Combine(state, "models", bundle.Model.File), "old cache");
            window = Open(package, state, bundle);
            try
            {
                var send = Button(window, "Send"); var start = Button(window, "Start local AI");
                Click(start); Until(() => send.IsEnabled || start.IsEnabled, 20000);
                Check(send.IsEnabled && server.Calls == 1, "GUI repairs a same-size corrupt model exactly once before starting inference");
                Check(File.ReadAllText(Path.Combine(package, "models", bundle.Model.File)) == "xxxxxxx" &&
                    File.ReadAllText(Path.Combine(state, "models", bundle.Model.File)) == "fixture",
                    "GUI model repair preserves the installed seed and publishes only a verified private cache");
            }
            finally { window.Close(); }
            window = Open(package, state, bundle);
            try
            {
                var send = Button(window, "Send"); var start = Button(window, "Start local AI");
                Click(start); Until(() => send.IsEnabled || start.IsEnabled, 20000);
                Check(send.IsEnabled && server.Calls == 1,
                    "GUI second launch uses the repaired cache offline despite the preserved damaged seed");
            }
            finally { window.Close(); }
            server.Stall = true;
            File.WriteAllText(Path.Combine(state, "models", bundle.Model.File), "old cache");
            window = Open(package, state, bundle);
            try
            {
                var start = Button(window, "Start local AI");
                Click(start); Until(() => server.Calls == 2 || start.IsEnabled, 10000);
                Click(Button(window, "Cancel")); Until(() => start.IsEnabled, 10000);
                Check(!Button(window, "Send").IsEnabled && File.ReadAllText(Path.Combine(state, "models", bundle.Model.File)) == "old cache" &&
                    Directory.GetFiles(Path.Combine(state, "models"), "*.partial-*").Length == 0,
                    "GUI cancellation during model repair keeps the old cache and leaves no usable runtime or partial model");
            }
            finally { window.Close(); }
            // Runtime corruption is never mistaken for permission to download a model.
            File.AppendAllText(Path.Combine(native, "llama-server.exe"), "damaged");
            bundle.RuntimeBundled = true;
            File.AppendAllText(Path.Combine(package, "runtime", "llama-server.exe"), "damaged");
            window = Open(package, state, bundle);
            try
            {
                var start = Button(window, "Start local AI");
                Click(start); Until(() => start.IsEnabled, 10000);
                Check(server.Calls == 2 && !Button(window, "Send").IsEnabled,
                    "GUI runtime integrity failure never triggers model repair or inference");
            }
            finally { window.Close(); }
        }
        return checks;
    }
    public static int Live(string package, string state, string screenshot)
    {
        if (Application.Current == null) new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
        SynchronizationContext.SetSynchronizationContext(new DispatcherSynchronizationContext());
        // Exercise the exact compiled package, not a second copy of its source.
        var product = Assembly.LoadFrom(Path.Combine(package, "ODS Windows Preview.exe"));
        var manifest = product.GetType("OdsDesktop.Bundle").GetMethod("Read").Invoke(null, new object[] { package });
        var window = (Window)Activator.CreateInstance(product.GetType("OdsDesktop.ChatWindow"), new object[] { package, state, manifest });
        window.ShowInTaskbar = false; window.Left = -10000; window.Top = -10000;
        window.WindowStartupLocation = WindowStartupLocation.Manual; window.Show();
        try
        {
            var send = Button(window, "Send");
            var start = Find<Button>(window, button => ((string)button.Content).Contains("start") || (string)button.Content == "Start local AI");
            var clock = Stopwatch.StartNew();
            Click(start); Until(() => send.IsEnabled || start.IsEnabled, 900000);
            if (!send.IsEnabled)
            {
                string details = String.Join("\n", ((StackPanel)((Grid)window.Content).Children[1]).Children.OfType<TextBlock>().Select(block => block.Text));
                throw new Exception("GUI live setup failed: " + details);
            }
            Console.WriteLine("LIVE GUI setup ready in " + clock.Elapsed.TotalSeconds.ToString("F1") + " seconds");
            var input = Find<TextBox>(window, box => true);
            input.Text = "What is 2+2? Answer with the number only."; Click(send);
            Until(() => input.IsEnabled || start.IsEnabled, 180000);
            var viewer = Find<FlowDocumentScrollViewer>(window, item => true);
            string transcript = new TextRange(viewer.Document.ContentStart, viewer.Document.ContentEnd).Text;
            Check(transcript.Contains("ODS\r\n4") || transcript.Contains("ODS\n4"), "real GUI completes the exact-token model probe");
            input.Text = "Hãy chào tôi bằng một câu tiếng Việt."; Click(send);
            Until(() => input.IsEnabled || start.IsEnabled, 180000);
            Check(input.Text == "", "real GUI completes a second Vietnamese chat turn");
            Console.WriteLine("LIVE GUI transcript: " + new TextRange(viewer.Document.ContentStart, viewer.Document.ContentEnd).Text);
            if (!String.IsNullOrEmpty(screenshot))
            {
                var content = (FrameworkElement)window.Content;
                content.UpdateLayout();
                int width = (int)(content.ActualWidth + content.Margin.Left + content.Margin.Right);
                int height = (int)(content.ActualHeight + content.Margin.Top + content.Margin.Bottom);
                var bitmap = new RenderTargetBitmap(width, height, 96, 96, PixelFormats.Pbgra32);
                var background = new DrawingVisual();
                using (var drawing = background.RenderOpen())
                    drawing.DrawRectangle(window.Background, null, new Rect(0, 0, width, height));
                bitmap.Render(background);
                bitmap.Render(content);
                var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(bitmap));
                using (var output = File.Create(screenshot)) encoder.Save(output);
            }
        }
        finally { window.Close(); }
        return checks;
    }
}
