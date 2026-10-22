// SPDX-License-Identifier: Apache-2.0
using System;
using System.Diagnostics;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.Wpf;
using Microsoft.Win32;

namespace OdsDesktop
{
    public static class WebView2Runtime
    {
        // Full API compatibility for the pinned Microsoft.Web.WebView2 1.0.4258.31 SDK:
        // https://learn.microsoft.com/microsoft-edge/webview2/release-notes/sdk/1-0-4258-31
        public const string MinimumVersion = "154.0.4258.31";
        public static bool Supports(string browserVersion)
        {
            if (browserVersion == null || !System.Text.RegularExpressions.Regex.IsMatch(browserVersion,
                "\\A[0-9]{1,5}\\.[0-9]{1,5}\\.[0-9]{1,5}\\.[0-9]{1,5}(?: (?:beta|dev|canary))?\\z")) return false;
            Version actual;
            if (!Version.TryParse(browserVersion.Split(' ')[0], out actual) ||
                actual.Major > 65535 || actual.Minor > 65535 || actual.Build > 65535 || actual.Revision > 65535) return false;
            return actual >= new Version(MinimumVersion);
        }
        public static string PrerequisiteFor(string browserVersion)
        {
            return Supports(browserVersion) ? null : "Install or update Microsoft's WebView2 Runtime to " + MinimumVersion +
                " or later, then click Start again.";
        }
    }
    public sealed class PortalWindow : Window
    {
        readonly WebView2 view = new WebView2();
        readonly NativePortalSession session;
        readonly string assets, profile;
        readonly HashSet<string> allowedAssets;
        readonly TextBlock downloadStatus = new TextBlock { VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(12, 0, 0, 0) };
        bool closed;
        public bool RestartRequested { get; private set; }
        public static string RuntimePrerequisite()
        {
            try { return WebView2Runtime.PrerequisiteFor(CoreWebView2Environment.GetAvailableBrowserVersionString()); }
            catch (WebView2RuntimeNotFoundException) { return WebView2Runtime.PrerequisiteFor(null); }
        }
        public PortalWindow(IPortalModel model, string package, string state, string modelName)
        {
            Title = "ODS Portal — Windows Preview"; Width = 1180; Height = 820; MinWidth = 760; MinHeight = 560;
            WindowStartupLocation = WindowStartupLocation.CenterScreen;
            string root = PortalState.PrivateDirectory(Path.Combine(state, "portal"));
            profile = PortalState.PrivateDirectory(Path.Combine(root, "browser"));
            session = new NativePortalSession(model, Path.Combine(root, "results"), modelName);
            assets = Path.Combine(package, "portal");
            allowedAssets = new HashSet<string>(PortalPackage.ReadManifest(package).Where(pin => pin.Name.StartsWith("portal/", StringComparison.Ordinal))
                .Select(pin => pin.Name.Substring(7)), StringComparer.Ordinal);
            var dock = new DockPanel();
            var toolbar = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(8) };
            var restart = new Button { Content = "Restart local AI", Padding = new Thickness(12, 5, 12, 5) };
            restart.Click += (sender, args) => { RestartRequested = true; Close(); };
            toolbar.Children.Add(restart);
            toolbar.Children.Add(new TextBlock { Text = "  Native Windows · local CPU · saved conversations", VerticalAlignment = VerticalAlignment.Center });
            toolbar.Children.Add(downloadStatus);
            DockPanel.SetDock(toolbar, Dock.Top); dock.Children.Add(toolbar); dock.Children.Add(view); Content = dock;
            Loaded += async (sender, args) => await InitializeAsync();
            Closed += (sender, args) => { closed = true; try { session.Dispose(); } finally { view.Dispose(); } };
        }
        async Task InitializeAsync()
        {
            try
            {
                var options = new CoreWebView2EnvironmentOptions();
                options.ExclusiveUserDataFolderAccess = true;
                options.TargetCompatibleBrowserVersion = WebView2Runtime.MinimumVersion;
                var environment = await CoreWebView2Environment.CreateAsync(null, profile, options);
                if (closed) return;
                string prerequisite = WebView2Runtime.PrerequisiteFor(environment.BrowserVersionString);
                if (prerequisite != null) throw new InvalidOperationException(prerequisite);
                await view.EnsureCoreWebView2Async(environment);
                var core = view.CoreWebView2;
                core.Settings.AreHostObjectsAllowed = false;
                core.Settings.IsWebMessageEnabled = false;
                core.Settings.AreDevToolsEnabled = false;
                core.Settings.IsPasswordAutosaveEnabled = false;
                core.Settings.IsGeneralAutofillEnabled = false;
                core.Settings.AreDefaultContextMenusEnabled = false;
                core.PermissionRequested += (sender, args) => { args.State = CoreWebView2PermissionState.Deny; args.Handled = true; };
                core.DownloadStarting += (sender, args) => SaveDocument(args);
                core.NavigationStarting += (sender, args) =>
                {
                    Uri target;
                    if (!Uri.TryCreate(args.Uri, UriKind.Absolute, out target) || target.GetLeftPart(UriPartial.Authority) != NativePortalSession.Origin)
                        args.Cancel = true;
                };
                core.NewWindowRequested += (sender, args) =>
                {
                    args.Handled = true;
                    Uri target;
                    if (args.IsUserInitiated && Uri.TryCreate(args.Uri, UriKind.Absolute, out target) && (target.Scheme == "https" || target.Scheme == "http"))
                        Process.Start(new ProcessStartInfo(target.AbsoluteUri) { UseShellExecute = true });
                };
                core.AddWebResourceRequestedFilter("*", CoreWebView2WebResourceContext.All);
                core.WebResourceRequested += async (sender, args) => await ResourceAsync(environment, args);
                core.Navigate(NativePortalSession.Origin + "/");
            }
            catch (Exception error)
            {
                if (!closed) Content = new TextBlock { Text = "Portal could not open. Your saved conversations are kept.\n" + error.Message,
                    TextWrapping = TextWrapping.Wrap, Margin = new Thickness(30) };
            }
        }
        public static bool CanSaveDocument(string documentUri, string downloadUri, ulong? bytes)
        {
            Uri document;
            return Uri.TryCreate(documentUri, UriKind.Absolute, out document) && document.UserInfo.Length == 0 &&
                document.GetLeftPart(UriPartial.Authority) == NativePortalSession.Origin && downloadUri != null &&
                downloadUri.StartsWith("blob:" + NativePortalSession.Origin + "/", StringComparison.Ordinal) &&
                (!bytes.HasValue || bytes.Value > 0 && bytes.Value <= NativeDocument.MaximumBytes);
        }
        public static string DownloadFileName(string documentUri, string downloadUri, string mimeType, string suggestedPath, ulong? bytes)
        {
            Uri document;
            if (!Uri.TryCreate(documentUri, UriKind.Absolute, out document) || document.UserInfo.Length != 0 ||
                document.GetLeftPart(UriPartial.Authority) != NativePortalSession.Origin || downloadUri == null ||
                !downloadUri.StartsWith("blob:" + NativePortalSession.Origin + "/", StringComparison.Ordinal) || bytes == 0) return null;
            string filename;
            try { filename = Path.GetFileName(suggestedPath ?? ""); }
            catch (ArgumentException) { return null; }
            if (mimeType == "application/octet-stream" && filename == NativeDocument.FileName && CanSaveDocument(documentUri, downloadUri, bytes))
                return NativeDocument.FileName;
            // Existing Portal export is also a private Blob download. Preserve
            // its JSON name and complete history, including exports >64 KiB.
            if (mimeType == "application/json" && System.Text.RegularExpressions.Regex.IsMatch(filename, "\\Aods-portal-[A-Za-z0-9_-]{1,128}\\.json\\z"))
                return filename;
            return null;
        }
        void SaveDocument(CoreWebView2DownloadStartingEventArgs args)
        {
            // File creation is an owner choice, never a model path or a silent
            // browser write. Generated Markdown is downloaded, never executed.
            args.Handled = true; args.Cancel = true;
            if (closed) return;
            string filename = DownloadFileName(view.CoreWebView2.Source, args.DownloadOperation.Uri, args.DownloadOperation.MimeType,
                args.ResultFilePath, args.DownloadOperation.TotalBytesToReceive);
            if (filename == null) return;
            // Microsoft forbids nested modal loops within WebView2 callbacks.
            // Retain the download decision and return from DownloadStarting
            // before opening the owner's native picker on the WPF dispatcher.
            // https://learn.microsoft.com/dotnet/api/microsoft.web.webview2.core.corewebview2.downloadstarting
            var deferred = args.GetDeferral();
            try
            {
                Dispatcher.BeginInvoke(new Action(() =>
                {
                    try { using (deferred) { if (!closed) ChooseDownload(args, filename); } }
                    catch (Exception)
                    {
                        if (!closed) downloadStatus.Text = "The download could not start. Your saved content is kept; try Download or Export again.";
                    }
                }));
            }
            catch (Exception)
            {
                deferred.Dispose();
                if (!closed) downloadStatus.Text = "The download could not start. Your saved content is kept; try Download or Export again.";
            }
        }
        void ChooseDownload(CoreWebView2DownloadStartingEventArgs args, string filename)
        {
            bool markdown = filename == NativeDocument.FileName;
            try
            {
                var dialog = new SaveFileDialog { Title = markdown ? "Save document" : "Save conversation", FileName = filename,
                    Filter = markdown ? "Markdown document (*.md)|*.md" : "ODS conversation (*.json)|*.json", DefaultExt = markdown ? ".md" : ".json", AddExtension = true,
                    OverwritePrompt = true, CheckPathExists = true, ValidateNames = true };
                if (dialog.ShowDialog(this) != true) { downloadStatus.Text = "Download cancelled. No file was saved."; return; }
                args.ResultFilePath = dialog.FileName; args.Cancel = false;
                downloadStatus.Text = "Saving file…";
                var operation = args.DownloadOperation;
                EventHandler<object> completed = null;
                completed = (sender, eventArgs) =>
                {
                    if (operation.State == CoreWebView2DownloadState.InProgress) return;
                    operation.StateChanged -= completed;
                    if (!closed) downloadStatus.Text = operation.State == CoreWebView2DownloadState.Completed
                        ? "File saved to your chosen folder." : "The file could not be saved. Try Download or Export again.";
                };
                operation.StateChanged += completed;
            }
            catch (Exception)
            {
                // Native UI boundary; a failed picker never authorizes a write.
                args.Cancel = true;
                if (!closed) downloadStatus.Text = "The download could not start. Your saved content is kept; try Download or Export again.";
            }
        }
        async Task ResourceAsync(CoreWebView2Environment environment, CoreWebView2WebResourceRequestedEventArgs args)
        {
            using (var deferred = args.GetDeferral())
            {
                try
                {
                    var uri = new Uri(args.Request.Uri);
                    if (uri.GetLeftPart(UriPartial.Authority) != NativePortalSession.Origin || uri.UserInfo.Length != 0 || uri.Query.Length != 0)
                    { Respond(environment, args, 403, "text/plain", "External resource blocked."); return; }
                    if (uri.AbsolutePath.StartsWith("/api/", StringComparison.Ordinal))
                    {
                        string document = view.CoreWebView2.Source;
                        Uri page;
                        string documentOrigin = Uri.TryCreate(document, UriKind.Absolute, out page) ? page.GetLeftPart(UriPartial.Authority) : "";
                        string origin = args.Request.Headers.Contains("Origin") ? args.Request.Headers.GetHeader("Origin") : documentOrigin;
                        string content = "";
                        if (args.Request.Content != null)
                        {
                            using (var input = args.Request.Content)
                            using (var bytes = new MemoryStream())
                            {
                                var buffer = new byte[65536]; int read;
                                while ((read = await input.ReadAsync(buffer, 0, buffer.Length)) > 0)
                                {
                                    if (bytes.Length + read > 8 * 1024 * 1024) { Respond(environment, args, 413, "text/plain", "Request too large."); return; }
                                    bytes.Write(buffer, 0, read);
                                }
                                content = new UTF8Encoding(false, true).GetString(bytes.ToArray());
                            }
                        }
                        if (args.Request.Method == "POST" && (!args.Request.Headers.Contains("Content-Type") || args.Request.Headers.GetHeader("Content-Type") != "application/json"))
                        { Respond(environment, args, 415, "text/plain", "JSON request required."); return; }
                        var reply = await session.HandleAsync(documentOrigin, origin, args.Request.Method, uri.AbsolutePath, content);
                        Respond(environment, args, reply.Status, reply.Type, reply.Content);
                    }
                    else if (uri.AbsolutePath.StartsWith("/pixel-preview/", StringComparison.Ordinal))
                    {
                        Uri page;
                        string documentOrigin = Uri.TryCreate(view.CoreWebView2.Source, UriKind.Absolute, out page) ? page.GetLeftPart(UriPartial.Authority) : "";
                        string origin = args.Request.Headers.Contains("Origin") ? args.Request.Headers.GetHeader("Origin") : documentOrigin;
                        var document = session.ReadDocument(documentOrigin, origin, args.Request.Method, uri.AbsolutePath);
                        if (document == null) { Respond(environment, args, 404, "text/plain", "Document unavailable."); return; }
                        args.Response = environment.CreateWebResourceResponse(new MemoryStream(document.ReadBytes()), 200, "OK",
                            Headers("application/octet-stream") + "Content-Disposition: attachment; filename=\"document.md\"\r\n");
                    }
                    else
                    {
                        if (args.Request.Method != "GET") { Respond(environment, args, 405, "text/plain", "GET required."); return; }
                        string relative = Uri.UnescapeDataString(uri.AbsolutePath.TrimStart('/'));
                        if (relative.Length == 0) relative = "native.html";
                        if (!PortalPackage.SafePath(relative) || !allowedAssets.Contains(relative)) { Respond(environment, args, 404, "text/plain", "Resource unavailable."); return; }
                        string path = Path.Combine(assets, relative.Replace('/', Path.DirectorySeparatorChar));
                        if (!File.Exists(path)) { Respond(environment, args, 404, "text/plain", "Resource unavailable."); return; }
                        args.Response = environment.CreateWebResourceResponse(File.OpenRead(path), 200, "OK", Headers(ContentType(path)));
                    }
                }
                catch (Exception)
                {
                    // Private request boundary: never send a file path, model
                    // response, API key or exception text into the document.
                    Respond(environment, args, 500, "application/json", "{\"detail\":\"Native Portal could not complete the request. Restart local AI to recover.\"}");
                }
            }
        }
        void Respond(CoreWebView2Environment environment, CoreWebView2WebResourceRequestedEventArgs args, int status, string type, string content)
        {
            if (closed) return;
            args.Response = environment.CreateWebResourceResponse(new MemoryStream(Encoding.UTF8.GetBytes(content)), status,
                status == 200 ? "OK" : "Request failed", Headers(type));
        }
        static string Headers(string type)
        {
            return "Content-Type: " + type + "\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\n" +
                "Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; media-src 'self'; frame-src 'none'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'\r\n";
        }
        static string ContentType(string path)
        {
            switch (Path.GetExtension(path).ToLowerInvariant())
            {
                case ".html": return "text/html; charset=utf-8";
                case ".js": return "text/javascript; charset=utf-8";
                case ".css": return "text/css; charset=utf-8";
                case ".svg": return "image/svg+xml";
                case ".png": return "image/png";
                case ".ico": return "image/x-icon";
                case ".woff2": return "font/woff2";
                default: return "application/octet-stream";
            }
        }
    }
}
