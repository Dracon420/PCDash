# Saves an installed app's own icon as a PNG with transparency, as large as
# Windows has it (asks for 256 px). Uses the Start-menu AppID, so it works the
# same for Store/Xbox apps and ordinary programs:
#   app_icon.ps1 -AppId "<AppID from Get-StartApps>" -Out icon.png [-Size 256]
# Used by make_tile_art.py for the K-Touch app tiles (2026-11-12).
param([Parameter(Mandatory)] [string]$AppId, [Parameter(Mandatory)] [string]$Out, [int]$Size = 256)

Add-Type -ReferencedAssemblies System.Drawing @'
using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;

public static class AppIcon {
    [StructLayout(LayoutKind.Sequential)] public struct SIZE { public int cx, cy; }
    [ComImport, Guid("bcc18b79-ba16-442f-80c4-8a59c30c463b"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IShellItemImageFactory { [PreserveSig] int GetImage(SIZE size, int flags, out IntPtr phbm); }
    [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
    static extern void SHCreateItemFromParsingName(string path, IntPtr pbc, [MarshalAs(UnmanagedType.LPStruct)] Guid riid,
        [MarshalAs(UnmanagedType.Interface)] out IShellItemImageFactory ppv);
    [StructLayout(LayoutKind.Sequential)] struct BITMAP { public int bmType, bmWidth, bmHeight, bmWidthBytes; public ushort bmPlanes, bmBitsPixel; public IntPtr bmBits; }
    [DllImport("gdi32.dll")] static extern int GetObject(IntPtr h, int c, out BITMAP bm);
    [DllImport("gdi32.dll")] static extern bool DeleteObject(IntPtr h);

    public static void Save(string parsingName, string outPath, int size) {
        IShellItemImageFactory f;
        SHCreateItemFromParsingName(parsingName, IntPtr.Zero, new Guid("bcc18b79-ba16-442f-80c4-8a59c30c463b"), out f);
        IntPtr hbm;
        // 0x4 ICONONLY: the app's icon, not a thumbnail; 0x1 BIGGERSIZEOK.
        int hr = f.GetImage(new SIZE { cx = size, cy = size }, 0x4 | 0x1, out hbm);
        if (hr != 0) throw new Exception("GetImage failed 0x" + hr.ToString("X"));
        try {
            BITMAP bm; GetObject(hbm, Marshal.SizeOf(typeof(BITMAP)), out bm);
            int w = bm.bmWidth, h = Math.Abs(bm.bmHeight), stride = bm.bmWidthBytes;
            byte[] px = new byte[stride * h];
            Marshal.Copy(bm.bmBits, px, 0, px.Length);
            // DIB sections from the shell are bottom-up, 32 bpp premultiplied BGRA.
            using (var bmp = new Bitmap(w, h, PixelFormat.Format32bppPArgb)) {
                var d = bmp.LockBits(new Rectangle(0, 0, w, h), ImageLockMode.WriteOnly, PixelFormat.Format32bppPArgb);
                for (int y = 0; y < h; y++)
                    Marshal.Copy(px, (h - 1 - y) * stride, d.Scan0 + y * d.Stride, w * 4);
                bmp.UnlockBits(d);
                bmp.Save(outPath, ImageFormat.Png);
            }
        } finally { DeleteObject(hbm); }
    }
}
'@
[AppIcon]::Save("shell:AppsFolder\$AppId", $Out, $Size)
"saved $Out"
