// SPDX-License-Identifier: Apache-2.0
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Http;
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
        LocalRuntime runtime;
        CancellationTokenSource operation;
        bool closing;
        public ChatWindow(string directory, string state, Bundle bundle)
        {
            this.directory = directory; this.state = state; this.bundle = bundle;
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
            header.Children.Add(new TextBlock { Text = "Local chat preview · CPU · No Docker or WSL", Foreground = Brush("#4B5563"), FontSize = 14 });
            layout.Children.Add(header);
            var setup = new StackPanel { Margin = new Thickness(0, 0, 0, 16) };
            status.Text = "Ready to set up " + bundle.Model.Name;
            status.FontWeight = FontWeights.SemiBold; status.FontSize = 16;
            detail.Text = "Download the model once, then chat offline. Your conversations stay in memory and are cleared when you close the app.";
            detail.Margin = new Thickness(0, 6, 0, 10); detail.Foreground = Brush("#4B5563");
            setup.Children.Add(status); setup.Children.Add(detail);
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
            var note = new TextBlock { Text = "Enter to send · Shift+Enter for a new line · AI can make mistakes.\nThis preview covers local text chat. Portal, agents, voice and extensions are not included.",
                TextWrapping = TextWrapping.Wrap, FontSize = 12, Foreground = Brush("#4B5563"), Margin = new Thickness(0, 10, 0, 0) };
            Grid.SetRow(note, 4); layout.Children.Add(note);
            System.Windows.Automation.AutomationProperties.SetName(prompt, "Message to your local AI");
            start.Click += async (sender, args) => await StartAsync();
            prerequisites.Click += (sender, args) =>
            {
                try { Process.Start(new ProcessStartInfo(NativePrerequisites.HelpUrl) { UseShellExecute = true }); }
                catch (Win32Exception error) { detail.Text = error.Message + " Open " + NativePrerequisites.HelpUrl; }
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
            if (File.Exists(Path.Combine(state, "models", bundle.Model.File)) || File.Exists(Path.Combine(directory, "models", bundle.Model.File)))
                start.Content = "Start local AI";
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
            start.IsEnabled = !busy && runtime == null;
            send.IsEnabled = !busy && runtime != null;
            prompt.IsEnabled = !busy && runtime != null;
            fresh.IsEnabled = !busy && runtime != null;
            cancel.IsEnabled = busy;
            cancel.Visibility = busy ? Visibility.Visible : Visibility.Collapsed;
        }
        async Task StartAsync()
        {
            if (operation != null) return;
            if (!NativePrerequisites.Available(Environment.SystemDirectory))
            {
                status.Text = "One Windows component is needed first";
                detail.Text = "Install Microsoft's latest Visual C++ v14 x64 runtime, then click Start again. Windows may ask for administrator permission to install it.";
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
                    var feedback = new Progress<double>(value =>
                        {
                            if (closing || operation == null) return;
                            progress.IsIndeterminate = false; progress.Value = value * 100;
                            status.Text = "Downloading local AI files — " + Math.Floor(value * 100) + "%";
                        });
                    detail.Text = "Downloading the checked runtime from GitHub and model from Hugging Face. You can cancel at any time.";
                    string native = await RuntimeDownload.EnsureAsync(bundle, state, feedback, operation.Token);
                    string bundled = Path.Combine(directory, "models", bundle.Model.File);
                    string model = await ModelDownload.MatchesAsync(bundled, bundle.Model.Size, bundle.Model.Sha256, operation.Token)
                        ? bundled : await ModelDownload.EnsureAsync(bundle.Model, Path.Combine(state, "models"), feedback, operation.Token);
                    status.Text = "Starting your local AI…";
                    detail.Text = "Verifying the model and loading it into memory. This can take a few minutes.";
                    progress.IsIndeterminate = true;
                    runtime = new LocalRuntime();
                    await runtime.StartAsync(native, bundle, model, state, operation.Token);
                    status.Text = "Ready · " + bundle.Model.Name + " · running locally on CPU";
                    detail.Text = "You're offline-ready. Type a message below to begin.";
                    start.Content = "Start local AI";
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
                status.Text = "Ready · " + bundle.Model.Name + " · running locally on CPU";
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
            bool cancelled = operation != null && operation.IsCancellationRequested;
            if (runtime != null && (setup || cancelled || !runtime.IsRunning))
            { runtime.Dispose(); runtime = null; transcript.Blocks.Clear(); }
            if (closing) return;
            // This is the UI boundary: make every unexpected error visible and
            // leave the owner's unsent message available for recovery.
            status.Text = cancelled ? cancelledMessage : "Something needs your attention";
            detail.Text = cancelled ? "Your unsent message and completed download are kept. The earlier chat was cleared."
                : (error is OperationCanceledException ? "The operation timed out. Check your connection or try a shorter message." : error.Message);
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
            if (runtime != null) { runtime.Dispose(); runtime = null; }
        }
    }

    static class Program
    {
        [STAThread]
        static void Main()
        {
            bool first;
            using (var singleton = new Mutex(true, "Local\\ODSDesktopPreview-" + WindowsIdentity.GetCurrent().User.Value, out first))
            {
                if (!first) { MessageBox.Show("ODS Windows Preview is already open.", "ODS"); return; }
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
        }
    }
}
