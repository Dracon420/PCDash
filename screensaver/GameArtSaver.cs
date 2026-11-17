// GameArt screensaver for PC1. Build: build.ps1. Windows runs it as "GameArt.scr /s".
// Every monitor gets its own slideshow of official game art (game-art\store\<game>\*.jpg):
// slow pan-and-zoom, 2 s crossfades, and a small game name + clock label that moves to a
// different corner on every slide, so nothing stays in one place.
// Portrait monitors (the Onn) prefer poster art. Animation runs in WPF on the GPU.
// Any key, click or real mouse movement exits.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text.RegularExpressions;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using Forms = System.Windows.Forms;

static class Program
{
    public const string ArtRoot = @"D:\Servers\PCDash\game-art\store";
    public const string GamesJson = @"D:\Servers\PCDash\games.json";
    public const double SlideSeconds = 22, FadeSeconds = 2.0;

    [DllImport("user32.dll")] static extern bool SetProcessDPIAware();

    [STAThread]
    static void Main(string[] args)
    {
        string mode = args.Length > 0 ? args[0].ToLowerInvariant().TrimStart('/', '-') : "c";
        if (mode.StartsWith("p")) return;                       // little preview box: skip
        if (mode.StartsWith("c"))
        {
            Forms.MessageBox.Show("GameArt screensaver\n\nPictures come from:\n" + ArtRoot +
                "\n\nAdd or remove .jpg files in a game's folder to change what it shows.",
                "GameArt", Forms.MessageBoxButtons.OK, Forms.MessageBoxIcon.Information);
            return;
        }
        SetProcessDPIAware();
        var app = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
        var pool = ArtPool.Load();
        if (pool.Count == 0) return;
        var rng = new Random();
        int i = 0;
        foreach (var s in Forms.Screen.AllScreens)
            new SaverWindow(s.Bounds, pool, new Random(rng.Next()), i++).Show();
        app.Run();
    }
}

class Art { public string Path, Game, Title; public bool Portrait; }

static class ArtPool
{
    public static List<Art> Load()
    {
        var names = new Dictionary<string, string>();
        try
        {
            foreach (Match m in Regex.Matches(File.ReadAllText(Program.GamesJson),
                     "\"id\"\\s*:\\s*\"([^\"]+)\"\\s*,\\s*\"name\"\\s*:\\s*\"([^\"]+)\""))
                names[m.Groups[1].Value] = m.Groups[2].Value;
        }
        catch { }
        var list = new List<Art>();
        if (!Directory.Exists(Program.ArtRoot)) return list;
        foreach (var dir in Directory.GetDirectories(Program.ArtRoot))
        {
            string game = System.IO.Path.GetFileName(dir);
            string title = names.ContainsKey(game) ? names[game] : game;
            foreach (var f in Directory.GetFiles(dir, "*.jpg").Concat(Directory.GetFiles(dir, "*.png")))
            {
                string n = System.IO.Path.GetFileNameWithoutExtension(f).ToLowerInvariant();
                if (n == "boxart") continue;                     // square, logo-only
                list.Add(new Art { Path = f, Game = game, Title = title, Portrait = n == "poster" });
            }
        }
        return list;
    }
}

class SaverWindow : Window
{
    [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint f);
    static readonly IntPtr HWND_TOPMOST = new IntPtr(-1);

    readonly System.Drawing.Rectangle px;
    readonly List<Art> pool;
    readonly Random rng;
    readonly bool portrait;
    readonly Grid stage = new Grid { ClipToBounds = true, Background = Brushes.Black };
    readonly Border label;
    readonly TextBlock gameText, clockText;
    readonly DispatcherTimer slideTimer, clockTimer;
    string lastGame;
    int corner;
    Point? firstMouse;

    public SaverWindow(System.Drawing.Rectangle bounds, List<Art> pool, Random rng, int index)
    {
        px = bounds; this.pool = pool; this.rng = rng;
        portrait = bounds.Height > bounds.Width;
        corner = index % 4;
        WindowStyle = WindowStyle.None; ResizeMode = ResizeMode.NoResize;
        ShowInTaskbar = false; Topmost = true; Background = Brushes.Black; Cursor = System.Windows.Input.Cursors.None;
        Left = bounds.Left; Top = bounds.Top; Width = bounds.Width; Height = bounds.Height;

        gameText = new TextBlock { FontFamily = new FontFamily("Bahnschrift, Segoe UI"), FontSize = 30, FontWeight = FontWeights.SemiBold,
            Foreground = new SolidColorBrush(Color.FromRgb(0xfc, 0xee, 0x0a)) };
        clockText = new TextBlock { FontFamily = new FontFamily("Bahnschrift, Segoe UI"), FontSize = 56, FontWeight = FontWeights.Light,
            Foreground = new SolidColorBrush(Color.FromRgb(0x00, 0xf0, 0xff)) };
        var sp = new StackPanel(); sp.Children.Add(clockText); sp.Children.Add(gameText);
        label = new Border { Child = sp, Padding = new Thickness(26, 14, 26, 18), Margin = new Thickness(60),
            Background = new SolidColorBrush(Color.FromArgb(0x99, 0x04, 0x05, 0x09)),
            BorderBrush = new SolidColorBrush(Color.FromArgb(0xaa, 0x00, 0xf0, 0xff)), BorderThickness = new Thickness(1),
            Opacity = 0 };
        var root = new Grid(); root.Children.Add(stage); root.Children.Add(label);
        Content = root;

        clockTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(1) };
        clockTimer.Tick += (s, e) => clockText.Text = DateTime.Now.ToString("h:mm tt");
        clockTimer.Start(); clockText.Text = DateTime.Now.ToString("h:mm tt");
        slideTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(Program.SlideSeconds) };
        slideTimer.Tick += (s, e) => Next();

        SourceInitialized += (s, e) =>
            SetWindowPos(new WindowInteropHelper(this).Handle, HWND_TOPMOST, px.Left, px.Top, px.Width, px.Height, 0x0040);
        Loaded += (s, e) => { Next(); slideTimer.Start(); };

        KeyDown += (s, e) => Quit();
        MouseDown += (s, e) => Quit();
        MouseMove += (s, e) =>
        {
            var p = PointToScreen(e.GetPosition(this));
            if (firstMouse == null) firstMouse = p;
            else if (Math.Abs(p.X - firstMouse.Value.X) + Math.Abs(p.Y - firstMouse.Value.Y) > 12) Quit();
        };
    }

    static void Quit() { Application.Current.Shutdown(); }

    Art Pick()
    {
        // Portrait screens: posters twice as likely. Never the same game twice running.
        var cands = pool.Where(a => a.Game != lastGame || pool.All(b => b.Game == lastGame)).ToList();
        var weighted = new List<Art>();
        foreach (var a in cands)
        {
            int w = portrait ? (a.Portrait ? 6 : 1) : (a.Portrait ? 0 : 1);
            for (int k = 0; k < w; k++) weighted.Add(a);
        }
        if (weighted.Count == 0) weighted = cands;
        return weighted[rng.Next(weighted.Count)];
    }

    BitmapImage LoadImg(Art a)
    {
        var b = new BitmapImage();
        b.BeginInit();
        b.CacheOption = BitmapCacheOption.OnLoad;
        b.UriSource = new Uri(a.Path);
        // Decode only as big as needed (1.25x for the zoom), keeps memory small.
        if (portrait) b.DecodePixelHeight = (int)(px.Height * 1.25); else b.DecodePixelWidth = (int)(px.Width * 1.25);
        b.EndInit(); b.Freeze();
        return b;
    }

    void Next()
    {
        Art a = Pick();
        BitmapImage bmp;
        try { bmp = LoadImg(a); } catch { pool.Remove(a); if (pool.Count > 0) Next(); return; }
        lastGame = a.Game;

        var img = new Image { Source = bmp, Stretch = Stretch.UniformToFill, Opacity = 0,
            HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center,
            RenderTransformOrigin = new Point(0.5, 0.5) };
        RenderOptions.SetBitmapScalingMode(img, BitmapScalingMode.HighQuality);
        var scale = new ScaleTransform(); var move = new TranslateTransform();
        var tg = new TransformGroup(); tg.Children.Add(scale); tg.Children.Add(move);
        img.RenderTransform = tg;
        stage.Children.Add(img);

        // Ken Burns: zoom in or out between 1.04 and 1.16, drift up to 3% of the screen.
        double total = Program.SlideSeconds + Program.FadeSeconds + 1;
        bool zoomIn = rng.Next(2) == 0;
        double z0 = zoomIn ? 1.04 : 1.16, z1 = zoomIn ? 1.16 : 1.04;
        double dx = (rng.NextDouble() - 0.5) * 0.06 * ActualWidth, dy = (rng.NextDouble() - 0.5) * 0.06 * ActualHeight;
        var dur = new Duration(TimeSpan.FromSeconds(total));
        scale.BeginAnimation(ScaleTransform.ScaleXProperty, new DoubleAnimation(z0, z1, dur));
        scale.BeginAnimation(ScaleTransform.ScaleYProperty, new DoubleAnimation(z0, z1, dur));
        move.BeginAnimation(TranslateTransform.XProperty, new DoubleAnimation(-dx, dx, dur));
        move.BeginAnimation(TranslateTransform.YProperty, new DoubleAnimation(-dy, dy, dur));

        var fade = TimeSpan.FromSeconds(Program.FadeSeconds);
        img.BeginAnimation(OpacityProperty, new DoubleAnimation(0, 1, fade));
        // Retire older slides once the new one is fully in.
        var old = stage.Children.OfType<Image>().Where(x => x != img).ToList();
        var t = new DispatcherTimer { Interval = fade + TimeSpan.FromMilliseconds(200) };
        t.Tick += (s, e) => { t.Stop(); foreach (var o in old) stage.Children.Remove(o); };
        t.Start();

        // Label: fade out, move to the next corner, fade back in with the new name.
        var outA = new DoubleAnimation(label.Opacity, 0, TimeSpan.FromSeconds(0.8));
        outA.Completed += (s, e) =>
        {
            corner = (corner + 1 + rng.Next(3)) % 4;
            label.HorizontalAlignment = corner % 2 == 0 ? HorizontalAlignment.Left : HorizontalAlignment.Right;
            label.VerticalAlignment = corner < 2 ? VerticalAlignment.Bottom : VerticalAlignment.Top;
            gameText.Text = "\u25B8 " + a.Title.ToUpperInvariant();
            label.BeginAnimation(OpacityProperty, new DoubleAnimation(0, 1, TimeSpan.FromSeconds(1.2)));
        };
        label.BeginAnimation(OpacityProperty, outA);
    }
}
