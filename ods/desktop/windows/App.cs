// SPDX-License-Identifier: Apache-2.0
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Security.Principal;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Documents;
using System.Windows.Input;
using System.Windows.Media;

namespace OdsDesktop
{
    public sealed class ChatWindow : Window
    {
        readonly string directory, state;
        readonly Bundle bundle;
        readonly TextBlock status = new TextBlock { TextWrapping = TextWrapping.Wrap };
        readonly TextBlock detail = new TextBlock { TextWrapping = TextWrapping.Wrap };
        readonly ProgressBar progress = new ProgressBar { Height = 6, Maximum = 100, Visibility = Visibility.Collapsed };
        readonly TextBox prompt = new TextBox { AcceptsReturn = true, TextWrapping = TextWrapping.Wrap, MinHeight = 64,
            MaxHeight = 140, MaxLength = 4000, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, FontSize = 16, Padding = new Thickness(12) };
        readonly FlowDocument transcript = new FlowDocument { FontFamily = new FontFamily("Segoe UI"), FontSize = 16, PagePadding = new Thickness(20) };
        readonly Button start = Button("Download & start (1.30 GB)"), send = Button("Send"), cancel = Button("Cancel"), fresh = Button("New chat");
        readonly Button prerequisites = Button("Windows runtime help");
        readonly ComboBox acceleration = new ComboBox { MinWidth = 250, Margin = new Thickness(0, 0, 0, 10), Padding = new Thickness(8, 4, 8, 4) };
        bool cudaSelected, changingAcceleration;
        LocalRuntime runtime;
        CancellationTokenSource operation;
        bool closing;
        Window portal;
        string prerequisiteHelp = NativePrerequisites.HelpUrl;
        public ChatWindow(string directory, string state, Bundle bundle)
        {
            this.directory = directory; this.state = state; this.bundle = bundle;
            try { cudaSelected = NativeBackendSettings.ReadCuda(state); }
            catch (Exception error)
            {
                throw new InvalidOperationException("Could not read the saved acceleration choice at " + Path.Combine(state, "preferences", "backend.json") +
                    ". Existing settings were kept. Back up or repair that private settings file before reopening ODS. " + error.Message, error);
            }
            if (bundle.RuntimeBundled) start.Content = "Download & start (1.28 GB)";
            Title = "ODS — Windows Preview";
            Width = 940; Height = 740; MinWidth = 640; MinHeight = 520;
            WindowStartupLocation = WindowStartupLocation.CenterScreen;
            Background = Brush("#F4F6F8");
            FontFamily = new FontFamily("Segoe UI");
            var layout = new Grid { Margin = new Thickness(28) };
            layout.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            layout.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            layout.RowDefinitions.Add(new RowDefinition());
            layout.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            layout.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            Content = layout;
            var header = new StackPanel { Margin = new Thickness(0, 0, 0, 20) };
            header.Children.Add(new TextBlock { Text = "ODS", FontSize = 30, FontWeight = FontWeights.Bold, Foreground = Brush("#14664C") });
            header.Children.Add(new TextBlock { Text = "Your AI, on your Windows PC.", FontSize = 19, Margin = new Thickness(0, 4, 0, 6) });
            header.Children.Add(new TextBlock { Text = bundle.Cuda == null ? "Local chat preview · CPU · No Docker or WSL" : "Local chat preview · CPU or NVIDIA CUDA · No Docker or WSL", Foreground = Brush("#4B5563"), FontSize = 14 });
            layout.Children.Add(header);
            var setup = new StackPanel { Margin = new Thickness(0, 0, 0, 16) };
            status.Text = "Ready to set up " + bundle.Model.Name;
            status.FontWeight = FontWeights.SemiBold; status.FontSize = 16;
            detail.Text = String.IsNullOrEmpty(bundle.PortalSha256)
                ? "Download the model once, then chat offline. Your conversations stay in memory and are cleared when you close the app."
                : "Download the model once, then open ODS Portal. Conversations are saved privately on this Windows account.";
            detail.Margin = new Thickness(0, 6, 0, 10); detail.Foreground = Brush("#4B5563");
            setup.Children.Add(status); setup.Children.Add(detail);
            acceleration.Items.Add("CPU (default)"); acceleration.Items.Add("NVIDIA CUDA (opt-in)");
            acceleration.SelectedIndex = cudaSelected ? 1 : 0;
            System.Windows.Automation.AutomationProperties.SetName(acceleration, "Local AI acceleration");
            acceleration.SelectionChanged += (sender, args) =>
            {
                if (changingAcceleration) return;
                bool requested = acceleration.SelectedIndex == 1;
                try
                {
                    NativeBackendSettings.WriteCuda(state, requested); cudaSelected = requested;
                    status.Text = requested ? "NVIDIA CUDA selected" : "CPU selected";
                    detail.Text = requested ? "Start will check your NVIDIA GPU, acquire its pinned runtime and load the same local model. CPU remains available above."
                        : "Start uses the included CPU runtime. Your saved conversations and downloaded files are kept.";
                    UpdateStartLabel();
                }
                catch (Exception error)
                {
                    changingAcceleration = true; acceleration.SelectedIndex = cudaSelected ? 1 : 0; changingAcceleration = false;
                    status.Text = "The acceleration choice could not be saved"; detail.Text = error.Message;
                }
            };
            setup.Children.Add(acceleration);
            var actions = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 0, 0, 10) };
            actions.Children.Add(start); actions.Children.Add(cancel); actions.Children.Add(fresh);
            prerequisites.Visibility = Visibility.Collapsed; actions.Children.Add(prerequisites);
            setup.Children.Add(actions); setup.Children.Add(progress);
            Grid.SetRow(setup, 1); layout.Children.Add(setup);
            var viewer = new FlowDocumentScrollViewer { Document = transcript, VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
                Background = Brushes.White, IsToolBarVisible = false };
            Grid.SetRow(viewer, 2); layout.Children.Add(viewer);
            var compose = new DockPanel { Margin = new Thickness(0, 16, 0, 0), LastChildFill = true };
            DockPanel.SetDock(send, Dock.Right); compose.Children.Add(send); compose.Children.Add(prompt);
            Grid.SetRow(compose, 3); layout.Children.Add(compose);
            var note = new TextBlock { Text = String.IsNullOrEmpty(bundle.PortalSha256)
                ? "Enter to send · Shift+Enter for a new line · AI can make mistakes.\nThis preview covers local text chat. Portal, agents, voice and extensions are not included."
                : "AI can make mistakes. This preview opens the ODS Portal chat and document flow.\nAgent tools, voice, images and extensions are not included.",
                TextWrapping = TextWrapping.Wrap, FontSize = 12, Foreground = Brush("#4B5563"), Margin = new Thickness(0, 10, 0, 0) };
            Grid.SetRow(note, 4); layout.Children.Add(note);
            System.Windows.Automation.AutomationProperties.SetName(prompt, "Message to your local AI");
            start.Click += async (sender, args) => await StartAsync();
            prerequisites.Click += (sender, args) =>
            {
                try { Process.Start(new ProcessStartInfo(prerequisiteHelp) { UseShellExecute = true }); }
                catch (Win32Exception error) { detail.Text = error.Message + " Open " + prerequisiteHelp; }
            };
            send.Click += async (sender, args) => await SendAsync();
            prompt.PreviewKeyDown += async (sender, args) =>
            {
                if (args.Key == Key.Enter && Keyboard.Modifiers == ModifierKeys.None)
                { args.Handled = true; if (send.IsEnabled) await SendAsync(); }
            };
            cancel.Click += (sender, args) => { if (operation != null) operation.Cancel(); };
            fresh.Click += (sender, args) => { if (runtime != null) runtime.NewChat(); transcript.Blocks.Clear(); prompt.Focus(); };
            Closing += OnClosing;
            UpdateStartLabel();
            UpdateControls(false);
        }
        static SolidColorBrush Brush(string color) { return (SolidColorBrush)new BrushConverter().ConvertFromString(color); }
        static Button Button(string text)
        {
            return new Button { Content = text, Padding = new Thickness(18, 10, 18, 10), Margin = new Thickness(0, 0, 10, 0),
                Background = Brush("#14664C"), Foreground = Brushes.White, BorderThickness = new Thickness(0), FontSize = 14 };
        }
        void UpdateControls(bool busy)
        {
            acceleration.IsEnabled = !busy && runtime == null;
            start.IsEnabled = !busy && runtime == null;
            send.IsEnabled = !busy && runtime != null;
            prompt.IsEnabled = !busy && runtime != null;
            fresh.IsEnabled = !busy && runtime != null;
            cancel.IsEnabled = busy;
            cancel.Visibility = busy ? Visibility.Visible : Visibility.Collapsed;
            if (portal != null) { send.IsEnabled = false; prompt.IsEnabled = false; fresh.IsEnabled = false; }
        }
        void UpdateStartLabel()
        {
            bool cached = File.Exists(Path.Combine(state, "models", bundle.Model.File)) || File.Exists(Path.Combine(directory, "models", bundle.Model.File));
            start.Content = cached ? "Start local AI" : cudaSelected ? "Download & start (CUDA runtime + model)"
                : bundle.RuntimeBundled ? "Download & start (1.28 GB)" : "Download & start (1.30 GB)";
        }
        string ReadyStatus()
        { return "Ready · " + bundle.Model.Name + " · running locally on " + (runtime != null && runtime.IsCuda ? runtime.BackendDescription : "CPU"); }
        Task StartSelectedAsync(bool cuda, string native, Bundle launch, string model, CancellationToken token)
        {
            return cuda ? runtime.StartCudaAsync(native, launch, model, state, bundle.Cuda, token)
                : runtime.StartAsync(native, launch, model, state, token);
        }
        async Task StartAsync()
        {
            if (operation != null) return;
            bool useCuda = cudaSelected;
            if (useCuda && bundle.Cuda == null)
            {
                status.Text = "NVIDIA CUDA is unavailable in this package";
                detail.Text = "Choose CPU above or use a complete package containing the pinned CUDA runtime metadata. Your saved acceleration choice was kept.";
                return;
            }
            prerequisiteHelp = NativePrerequisites.HelpUrl;
            if ((useCuda || !bundle.RuntimeBundled) && !NativePrerequisites.Available(Environment.SystemDirectory))
            {
                status.Text = "One Windows component is needed first";
                detail.Text = "Install Microsoft's latest Visual C++ v14 x64 runtime, then click Start again. Windows may ask for administrator permission to install it." +
                    (useCuda ? " You can also choose CPU above to use the included CPU engine." : "");
                prerequisites.Visibility = Visibility.Visible;
                return;
            }
            prerequisites.Visibility = Visibility.Collapsed;
            operation = new CancellationTokenSource();
            UpdateControls(true);
            progress.Visibility = Visibility.Visible;
            progress.IsIndeterminate = true;
            status.Text = "Checking your model…";
            detail.Text = "First setup downloads " + bundle.Model.Name + ". You can cancel at any time.";
            try
            {
                {
                    Type portalType = null;
                    if (!String.IsNullOrEmpty(bundle.PortalSha256))
                    {
                        status.Text = "Checking your Portal package…";
                        await PortalPackage.VerifyAsync(directory, bundle.PortalSha256, operation.Token);
                        portalType = Assembly.LoadFrom(Path.Combine(directory, "OdsPortal.dll")).GetType("OdsDesktop.PortalWindow", true);
                        string prerequisite = (string)portalType.GetMethod("RuntimePrerequisite").Invoke(null, null);
                        if (prerequisite != null)
                        {
                            status.Text = "One Windows component is needed first"; detail.Text = prerequisite;
                            prerequisiteHelp = "https://developer.microsoft.com/en-us/microsoft-edge/webview2/";
                            prerequisites.Visibility = Visibility.Visible; return;
                        }
                    }
                    int downloadPhase = 1;
                    var pending = operation;
                    Action<double, string, int> report = (value, label, phase) =>
                    {
                        if (closing || operation != pending || downloadPhase != phase) return;
                        progress.IsIndeterminate = false; progress.Value = value * 100;
                        status.Text = label + " — " + Math.Floor(value * 100) + "%";
                    };
                    var engineFeedback = new Progress<double>(value => report(value, "Downloading the pinned runtime", 1));
                    var feedback = new Progress<double>(value => report(value, "Downloading your model", 2));
                    Bundle launch = bundle;
                    string native;
                    if (useCuda)
                    {
                        status.Text = "Checking your NVIDIA GPU…";
                        detail.Text = "Checking the Windows driver and supported CUDA compute capability before downloading the runtime.";
                        await CudaRuntime.CheckHardwareAsync(bundle.Cuda, state, operation.Token);
                        status.Text = "Preparing the NVIDIA CUDA runtime…";
                        detail.Text = "Acquiring the two pinned CUDA archives from ggml-org. You can cancel; completed verified downloads are kept.";
                        native = await CudaRuntimeDownload.EnsureAsync(bundle.Cuda, state, engineFeedback, operation.Token);
                        launch = CudaRuntimeDownload.ForLaunch(bundle, bundle.Cuda);
                    }
                    else
                    {
                        detail.Text = bundle.RuntimeBundled ? "Checking the included CPU runtime. You can cancel at any time."
                            : "Downloading the checked CPU runtime from GitHub. You can cancel at any time.";
                        native = bundle.RuntimeBundled ? RuntimeDownload.BundledCandidate(bundle, directory)
                            : await RuntimeDownload.EnsureAsync(bundle, state, engineFeedback, operation.Token);
                    }
                    downloadPhase = 2;
                    status.Text = "Preparing your model…";
                    detail.Text = "Using the checked local model or downloading it from Hugging Face. You can cancel at any time.";
                    string bundled = Path.Combine(directory, "models", bundle.Model.File);
                    string cached = Path.Combine(state, "models", bundle.Model.File);
                    bool hasSeed = ModelDownload.InspectPath(bundled, false) && new FileInfo(bundled).Length == bundle.Model.Size;
                    // Prefer a repaired private cache on later launches, so an
                    // intentionally preserved damaged seed cannot force a loop.
                    string model = ModelDownload.InspectPath(cached, false) && new FileInfo(cached).Length == bundle.Model.Size
                        ? cached : hasSeed ? bundled
                        : await ModelDownload.CandidateForLaunchAsync(bundle.Model, Path.Combine(state, "models"), feedback, operation.Token);
                    status.Text = "Starting your local AI…";
                    downloadPhase = 3;
                    detail.Text = useCuda ? "Verifying the runtime and model, qualifying the CUDA device and loading the model. CUDA is ready only after startup succeeds."
                        : "Verifying the model and loading it into memory. This can take a few minutes.";
                    progress.IsIndeterminate = true;
                    runtime = new LocalRuntime();
                    bool repairModel = false;
                    try { await StartSelectedAsync(useCuda, native, launch, model, operation.Token); }
                    catch (ModelIntegrityException) { repairModel = true; }
                    if (repairModel && model != bundled && hasSeed)
                    {
                        // A valid included seed remains an offline recovery path
                        // for a damaged cache. It passes the same launch gate.
                        repairModel = false;
                        try { await StartSelectedAsync(useCuda, native, launch, bundled, operation.Token); }
                        catch (ModelIntegrityException) { repairModel = true; }
                    }
                    if (repairModel)
                    {
                        // One repair retry, only for an ordinary bad model.
                        // Never rewrite the installed seed or repair unsafe paths,
                        // runtime corruption, access failures or failed inference.
                        operation.Token.ThrowIfCancellationRequested();
                        status.Text = "Repairing your model download…";
                        downloadPhase = 2;
                        model = await ModelDownload.DownloadReplacementAsync(bundle.Model, Path.Combine(state, "models"), feedback, operation.Token);
                        downloadPhase = 3;
                        progress.IsIndeterminate = true;
                        status.Text = "Starting your local AI…";
                        await StartSelectedAsync(useCuda, native, launch, model, operation.Token);
                    }
                    status.Text = ReadyStatus();
                    detail.Text = "You're offline-ready. Type a message below to begin.";
                    start.Content = "Start local AI";
                    if (portalType != null)
                    {
                        portal = (Window)Activator.CreateInstance(portalType, new object[] { runtime, directory, state, bundle.Model.Name });
                        portal.Closed += async (sender, args) =>
                        {
                            bool restart = (bool)portalType.GetProperty("RestartRequested").GetValue(portal, null);
                            portal = null;
                            if (closing) return;
                            if (!restart) { Close(); return; }
                            Show();
                            try { runtime.Dispose(); runtime = null; UpdateControls(false); await StartAsync(); }
                            catch (Exception error) { HandleFailure(error, "Restart stopped.", false); }
                        };
                        portal.Show(); Hide();
                    }
                }
            }
            catch (Exception error) { HandleFailure(error, "Setup stopped. You can start again.", true); }
            finally { EndOperation(); }
            if (!closing && runtime != null) prompt.Focus();
        }
        async Task SendAsync()
        {
            if (operation != null || runtime == null || String.IsNullOrWhiteSpace(prompt.Text)) return;
            string message = prompt.Text;
            operation = new CancellationTokenSource(); UpdateControls(true);
            status.Text = "Thinking on your PC…";
            try
            {
                string answer = await runtime.ChatAsync(message, operation.Token);
                AddMessage("You", message); AddMessage("ODS", answer);
                prompt.Clear();
                status.Text = ReadyStatus();
                detail.Text = "Your conversation is kept in memory until you start a new chat or close the app.";
            }
            catch (Exception error) { HandleFailure(error, "Response stopped. Start local AI to begin a new chat.", false); }
            finally { EndOperation(); }
            if (!closing && runtime != null) prompt.Focus();
        }
        void AddMessage(string author, string text)
        {
            var paragraph = new Paragraph { Margin = new Thickness(0, 0, 0, 20) };
            paragraph.Inlines.Add(new Bold(new Run(author + "\n")));
            paragraph.Inlines.Add(new Run(text));
            transcript.Blocks.Add(paragraph);
        }
        void HandleFailure(Exception error, string cancelledMessage, bool setup)
        {
            while (error is TargetInvocationException && error.InnerException != null) error = error.InnerException;
            bool cancelled = operation != null && operation.IsCancellationRequested;
            if (runtime != null && (setup || cancelled || !runtime.IsRunning))
            { runtime.Dispose(); runtime = null; transcript.Blocks.Clear(); }
            if (closing) return;
            // This is the UI boundary: make every unexpected error visible and
            // leave the owner's unsent message available for recovery.
            status.Text = cancelled ? cancelledMessage : "Something needs your attention";
            detail.Text = cancelled ? "Your unsent message and completed download are kept. The earlier chat was cleared."
                : (error is OperationCanceledException ? (setup
                    ? "Setup timed out. Check your connection and start again. Completed downloads are kept."
                    : "The response timed out. Try a shorter message or start a new chat.") : error.Message);
            if (setup && cudaSelected && !cancelled)
                detail.Text += " NVIDIA CUDA did not start. Choose CPU above or resolve this issue and retry; the acceleration choice was kept.";
        }
        void EndOperation()
        {
            operation.Dispose(); operation = null;
            if (closing) return;
            progress.Visibility = Visibility.Collapsed;
            UpdateControls(false);
        }
        void OnClosing(object sender, CancelEventArgs args)
        {
            closing = true;
            if (operation != null) operation.Cancel();
            try { if (portal != null) portal.Close(); }
            finally { if (runtime != null) { runtime.Dispose(); runtime = null; } }
        }
    }

    static class Program
    {
        [STAThread]
        static int Main(string[] args)
        {
            string mutexName = "Local\\ODSDesktopPreview-" + WindowsIdentity.GetCurrent().User.Value;
            // Installer-only probe: never loads a package, opens a window,
            // writes state or starts inference. The setup UI remains graphical.
            if (args.Length == 1 && args[0] == "--installation-check")
            {
                try
                {
                    Mutex existing;
                    if (!Mutex.TryOpenExisting(mutexName, out existing)) return 0;
                    existing.Dispose(); return 23;
                }
                catch (UnauthorizedAccessException) { return 24; }
            }
            bool first;
            using (var singleton = new Mutex(true, mutexName, out first))
            {
                if (!first) { MessageBox.Show("ODS Windows Preview is already open.", "ODS"); return 0; }
                try
                {
                    ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
                    string directory = AppDomain.CurrentDomain.BaseDirectory;
                    string state = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "ODS Desktop Preview");
                    var app = new Application();
                    app.Run(new ChatWindow(directory, state, Bundle.Read(directory)));
                }
                catch (Exception error) { MessageBox.Show(error.Message, "ODS could not open", MessageBoxButton.OK, MessageBoxImage.Error); }
                finally { singleton.ReleaseMutex(); }
            }
            return 0;
        }
    }
}
