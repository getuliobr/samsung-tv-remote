// TVShot: a tiny background service for Samsung Tizen TVs that serves screenshots over HTTP.
//
//   GET http://<tv-ip>:18765/ping                 -> "ok tizen=<version>"
//   GET http://<tv-ip>:18765/screenshot?w=960&h=540
//       -> raw frame: Y plane followed by UV plane (NV12 for format 0)
//          headers: X-Width, X-Height, X-Format (0=YUV420,1=YUV422,2=YUV444), X-YSize, X-UVSize
//
// Capture uses Samsung's private libsec-video-capture (Tizen <= 7) or libvideo-capture (Tizen 8+),
// the same calls HyperTizen uses for ambient lighting. DRM-protected video returns error -4.
using System;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using Tizen.Applications;

namespace TVShot
{
    [StructLayout(LayoutKind.Sequential)]
    public struct CaptureInfo
    {
        public int iGivenBufferSize1;   // Y plane buffer size
        public int iGivenBufferSize2;   // UV plane buffer size
        public int iWidth;
        public int iHeight;
        public IntPtr pImageY;
        public IntPtr pImageUV;
        public int iRetColorFormat;     // 0=YUV420, 1=YUV422, 2=YUV444
        public int unknown2;
        public int capture3DMode;
    }

    public static unsafe class Capture
    {
        [DllImport("/usr/lib/libsec-video-capture.so.0", CallingConvention = CallingConvention.Cdecl,
                   EntryPoint = "secvideo_api_capture_screen")]
        private static extern int CaptureT7(int w, int h, IntPtr info);

        [DllImport("/usr/lib/libsec-video-capture.so.0", CallingConvention = CallingConvention.Cdecl,
                   EntryPoint = "secvideo_api_capture_screen_video_only_crop")]
        private static extern int CropVideoOnly(int w, int h, IntPtr info, int cx, int cy, int cw, int ch);

        [DllImport("/usr/lib/libsec-video-capture.so.0", CallingConvention = CallingConvention.Cdecl,
                   EntryPoint = "secvideo_api_capture_video_main_crop")]
        private static extern int CropVideoMain(int w, int h, IntPtr info, int cx, int cy, int cw, int ch);

        [DllImport("/usr/lib/libsec-video-capture.so.0", CallingConvention = CallingConvention.Cdecl,
                   EntryPoint = "secvideo_api_capture_video_main")]
        private static extern int VideoMain(int w, int h, IntPtr info);

        [DllImport("/usr/lib/libsec-video-capture.so.0", CallingConvention = CallingConvention.Cdecl,
                   EntryPoint = "secvideo_api_capture_screen_video_only")]
        private static extern int ScreenVideoOnly(int w, int h, IntPtr info);

        [DllImport("/usr/lib/libsec-video-capture.so.0", CallingConvention = CallingConvention.Cdecl,
                   EntryPoint = "secvideo_api_capture_screen_lock")]
        public static extern int ScreenLock();

        [DllImport("/usr/lib/libsec-video-capture.so.0", CallingConvention = CallingConvention.Cdecl,
                   EntryPoint = "secvideo_api_capture_screen_unlock")]
        public static extern int ScreenUnlock();

        /// Experimental: fn = screen | vonly | vmain | vonly_crop | vmain_crop
        public static (int, CaptureInfo, byte[], byte[]) GrabFn(string fn, int w, int h, int cx, int cy, int cw, int ch)
        {
            CurrentFn = fn; CropArgs = new[] { cx, cy, cw, ch };
            try { return Grab(w, h); } finally { CurrentFn = "screen"; }
        }
        [ThreadStatic] private static string CurrentFn;
        [ThreadStatic] private static int[] CropArgs;

        [UnmanagedFunctionPointer(CallingConvention.ThisCall)]
        private delegate int CaptureT8Delegate(IntPtr self, int w, int h, IntPtr info);

        [DllImport("/usr/lib/libvideo-capture.so.0.1.0", CallingConvention = CallingConvention.Cdecl,
                   EntryPoint = "getInstance")]
        private static extern IntPtr GetInstanceT8();

        private static readonly object Lock = new object();
        public static string LastRaw = "";
        public static string Stage = "";
        private static IntPtr _t8Instance = IntPtr.Zero;
        private static CaptureT8Delegate _t8Capture;

        public static int TizenMajor
        {
            get
            {
                Tizen.System.Information.TryGetValue("http://tizen.org/feature/platform.version", out string v);
                return int.TryParse((v ?? "0").Split('.')[0], out int major) ? major : 0;
            }
        }

        public static string TizenVersion
        {
            get
            {
                Tizen.System.Information.TryGetValue("http://tizen.org/feature/platform.version", out string v);
                return v ?? "?";
            }
        }

        private static int CallCapture(int w, int h, IntPtr info)
        {
            if (TizenMajor >= 8)
            {
                if (_t8Instance == IntPtr.Zero)
                {
                    _t8Instance = GetInstanceT8();
                    if (_t8Instance == IntPtr.Zero) return -1000;
                    IntPtr vtable = *(IntPtr*)_t8Instance;
                    IntPtr fn = ((IntPtr*)vtable)[3];
                    _t8Capture = Marshal.GetDelegateForFunctionPointer<CaptureT8Delegate>(fn);
                }
                return _t8Capture(_t8Instance, w, h, info);
            }
            switch (CurrentFn)
            {
                case "vonly": return ScreenVideoOnly(w, h, info);
                case "vmain": return VideoMain(w, h, info);
                case "vonly_crop": return CropVideoOnly(w, h, info, CropArgs[0], CropArgs[1], CropArgs[2], CropArgs[3]);
                case "vmain_crop": return CropVideoMain(w, h, info, CropArgs[0], CropArgs[1], CropArgs[2], CropArgs[3]);
                default: return CaptureT7(w, h, info);
            }
        }

        /// Returns (result code, info, Y bytes, UV bytes).
        public static (int, CaptureInfo, byte[], byte[]) Grab(int w, int h)
        {
            lock (Lock)
            {
                Stage = "sizes"; int ySize = w * h;
                int uvSize = w * h * 2;   // big enough for 4:2:0, 4:2:2 and 4:4:4
                Stage = "alloc"; IntPtr pY = Marshal.AllocHGlobal(ySize);
                IntPtr pUV = Marshal.AllocHGlobal(uvSize);
                try
                {
                    // Native struct may be larger than documented: give it 1 KB of zeroed room.
                    IntPtr pInfo = Marshal.AllocHGlobal(1024);
                    for (int i = 0; i < 1024; i += 4) Marshal.WriteInt32(pInfo, i, 0);
                    int ps = IntPtr.Size;
                    Marshal.WriteInt32(pInfo, 0, ySize);
                    Marshal.WriteInt32(pInfo, 4, uvSize);
                    Marshal.WriteIntPtr(pInfo, 16, pY);
                    Marshal.WriteIntPtr(pInfo, 16 + ps, pUV);
                    var info = new CaptureInfo();
                    Stage = "capture(tizen=" + TizenVersion + ")"; int res = CallCapture(w, h, pInfo); info.iWidth = Marshal.ReadInt32(pInfo, 8); info.iHeight = Marshal.ReadInt32(pInfo, 12); info.iRetColorFormat = Marshal.ReadInt32(pInfo, 16 + 2 * ps); info.unknown2 = Marshal.ReadInt32(pInfo, 20 + 2 * ps); info.capture3DMode = Marshal.ReadInt32(pInfo, 24 + 2 * ps); Marshal.FreeHGlobal(pInfo); Stage = "after-capture res=" + res + " w=" + info.iWidth + " h=" + info.iHeight + " fmt=" + info.iRetColorFormat;
                    if (res < 0)
                    {
                        LastRaw = $"res={res} w={info.iWidth} h={info.iHeight} fmt={info.iRetColorFormat}";
                        return (res, info, null, null);
                    }

                    LastRaw = $"res={res} w={info.iWidth} h={info.iHeight} fmt={info.iRetColorFormat} u2={info.unknown2} 3d={info.capture3DMode} b1={info.iGivenBufferSize1} b2={info.iGivenBufferSize2}";
                    int rw = info.iWidth > 0 && info.iWidth <= w ? info.iWidth : w;
                    int rh = info.iHeight > 0 && info.iHeight <= h ? info.iHeight : h;
                    if (info.iRetColorFormat < 0 || info.iRetColorFormat > 2) info.iRetColorFormat = 0;
                    int ry = Math.Min(rw * rh, ySize);
                    int ruv;
                    switch (info.iRetColorFormat)
                    {
                        case 1: ruv = rw * rh; break;       // 4:2:2
                        case 2: ruv = rw * rh * 2; break;   // 4:4:4
                        default: ruv = rw * rh / 2; break;  // 4:2:0 (NV12)
                    }
                    ruv = Math.Min(ruv, uvSize);
                    Stage += $" ry={ry} ruv={ruv}"; var y = new byte[ry];
                    var uv = new byte[ruv];
                    Marshal.Copy(pY, y, 0, ry);
                    Marshal.Copy(pUV, uv, 0, ruv);
                    info.iWidth = rw;
                    info.iHeight = rh;
                    return (res, info, y, uv);
                }
                finally
                {
                    Marshal.FreeHGlobal(pY);
                    Marshal.FreeHGlobal(pUV);
                }
            }
        }
    }

    /// Experimental: call Samsung's C++ capture methods directly with our own InputParams (incl. crop).
    /// Layout reverse-engineered from libvideo-capture.so setParameters()/updateParameters() (ARM32):
    ///   InputParams : +0 cropX, +4 cropY, +8 cropW, +0xC cropH, +0x10 outW, +0x14 outH, +0x18..0x1B flags
    ///   OutputParams: +0x28 ySize, +0x2C cSize, +0x30 pY, +0x34 pC, +0x38 outW, +0x3C outH (filled by callee)
    public static class Capture2
    {
        [DllImport("libdl.so.2")] private static extern IntPtr dlopen(string file, int flags);
        [DllImport("libdl.so.2")] private static extern IntPtr dlsym(IntPtr handle, string name);
        [DllImport("libdl.so.2")] private static extern IntPtr dlerror();

        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate IntPtr GetInstanceFn();
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int MethodFn(IntPtr self, IntPtr inParams, IntPtr outParams);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int CropFn(int w, int h, IntPtr param, int cx, int cy, int cw, int ch);

        private static readonly object Lock = new object();
        private static IntPtr _vc, _impl, _instance;
        public static string ImplStatus = "";

        private static string Err() { var p = dlerror(); return p == IntPtr.Zero ? "" : Marshal.PtrToStringAnsi(p); }

        private static void Init()
        {
            if (_instance != IntPtr.Zero) return;
            // Load the Samsung implementation first: its static constructor injects the real builder.
            try { _impl = System.Runtime.InteropServices.NativeLibrary.Load("/usr/lib/libvideo-capture-impl-sec.so.0.1.0"); ImplStatus = "impl loaded"; }
            catch (Exception ex) { ImplStatus = "impl load failed: " + ex.Message; }
            _vc = dlopen("/usr/lib/libvideo-capture.so.0.1.0", 2);
            if (_vc == IntPtr.Zero) throw new Exception("dlopen libvideo-capture: " + Err());
            var gi = dlsym(_vc, "_ZN13IVideoCapture11getInstanceEv");
            if (gi == IntPtr.Zero) throw new Exception("dlsym getInstance: " + Err());
            _instance = Marshal.GetDelegateForFunctionPointer<GetInstanceFn>(gi)();
            if (_instance == IntPtr.Zero) throw new Exception("getInstance returned null");
        }

        public static string Debug()
        {
            lock (Lock)
            {
                Init();
                var sb = new StringBuilder();
                sb.Append(ImplStatus).Append('\n');
                sb.Append($"instance=0x{_instance.ToInt64():x}\n");
                IntPtr vt = Marshal.ReadIntPtr(_instance);
                sb.Append($"vtable=0x{vt.ToInt64():x}\n");
                for (int i = 0; i < 40; i++)
                    sb.Append($"vt[{i}]=0x{Marshal.ReadIntPtr(vt, i * IntPtr.Size).ToInt64():x}\n");
                foreach (var line in File.ReadAllLines("/proc/self/maps"))
                    if (line.Contains("capture")) sb.Append(line).Append('\n');
                return sb.ToString();
            }
        }

        public static int CallVtable(int index, IntPtr pIn, IntPtr pOut)
        {
            IntPtr vt = Marshal.ReadIntPtr(_instance);
            var fn = Marshal.ReadIntPtr(vt, index * IntPtr.Size);
            return Marshal.GetDelegateForFunctionPointer<MethodFn>(fn)(_instance, pIn, pOut);
        }

        /// method: getScreenPostYUV | getVideoPostYUV | getVideoMainYUV | getCroppedVideoPostYUV | getCroppedVideoMainYUV | vonly_crop
        public static (int res, int w, int h, byte[] y, byte[] c, string dbg) Grab(string method, int w, int h, int cx, int cy, int cw, int ch)
        {
            lock (Lock)
            {
                Init();
                int ySize = w * h, cSize = w * h * 2;
                IntPtr pY = Marshal.AllocHGlobal(ySize), pC = Marshal.AllocHGlobal(cSize);
                IntPtr pIn = Marshal.AllocHGlobal(256), pOut = Marshal.AllocHGlobal(1024);
                try
                {
                    for (int i = 0; i < 256; i += 4) Marshal.WriteInt32(pIn, i, 0);
                    for (int i = 0; i < 1024; i += 4) Marshal.WriteInt32(pOut, i, 0);
                    int res, ow, oh;
                    if (method == "vonly_crop")
                    {
                        // secvideo_capture_param layout (same as CaptureInfo, but oversized buffer)
                        Marshal.WriteInt32(pOut, 0, ySize); Marshal.WriteInt32(pOut, 4, cSize);
                        Marshal.WriteIntPtr(pOut, 0x10, pY); Marshal.WriteIntPtr(pOut, 0x14, pC);
                        var f = dlsym(_vc, "secvideo_api_capture_screen_video_only_crop");
                        res = Marshal.GetDelegateForFunctionPointer<CropFn>(f)(w, h, pOut, cx, cy, cw, ch);
                        ow = Marshal.ReadInt32(pOut, 8); oh = Marshal.ReadInt32(pOut, 0xC);
                    }
                    else
                    {
                        Marshal.WriteInt32(pIn, 0x00, cx); Marshal.WriteInt32(pIn, 0x04, cy);
                        Marshal.WriteInt32(pIn, 0x08, cw); Marshal.WriteInt32(pIn, 0x0C, ch);
                        Marshal.WriteInt32(pIn, 0x10, w); Marshal.WriteInt32(pIn, 0x14, h);
                        Marshal.WriteInt32(pOut, 0x28, ySize); Marshal.WriteInt32(pOut, 0x2C, cSize);
                        Marshal.WriteIntPtr(pOut, 0x30, pY); Marshal.WriteIntPtr(pOut, 0x34, pC);
                        // method is "vt<N>": call virtual slot N on the CaptureSecImpl instance
                        res = CallVtable(int.Parse(method.Substring(2)), pIn, pOut);
                        ow = Marshal.ReadInt32(pOut, 0x38); oh = Marshal.ReadInt32(pOut, 0x3C);
                    }
                    var sb = new StringBuilder();
                    for (int i = 0; i < 0x50; i += 4) sb.Append(Marshal.ReadInt32(pOut, i)).Append(i == 0x4C ? "" : ",");
                    string dbg = $"res={res} ow={ow} oh={oh} out=[{sb}]";
                    if (res < 0 || ow <= 0 || oh <= 0 || ow > w || oh > h) return (res, ow, oh, null, null, dbg);
                    var y = new byte[ow * oh]; var c = new byte[ow * oh / 2];
                    Marshal.Copy(pY, y, 0, y.Length); Marshal.Copy(pC, c, 0, c.Length);
                    return (res, ow, oh, y, c, dbg);
                }
                finally
                {
                    Marshal.FreeHGlobal(pY); Marshal.FreeHGlobal(pC); Marshal.FreeHGlobal(pIn); Marshal.FreeHGlobal(pOut);
                }
            }
        }
    }

    public static class HttpServer
    {
        public const int Port = 18765;

        public static void Run()
        {
            var listener = new TcpListener(IPAddress.Any, Port);
            listener.Start();
            while (true)
            {
                TcpClient client = null;
                try
                {
                    client = listener.AcceptTcpClient();
                    var c = client;
                    ThreadPool.QueueUserWorkItem(_ => Handle(c));
                }
                catch (Exception ex)
                {
                    Tizen.Log.Error("TVShot", "accept: " + ex.Message);
                    client?.Close();
                    Thread.Sleep(200);
                }
            }
        }

        private static void Handle(TcpClient client)
        {
            using (client)
            {
                try
                {
                    client.ReceiveTimeout = 5000;
                    var stream = client.GetStream();
                    var reader = new StreamReader(stream, Encoding.ASCII);
                    string requestLine = reader.ReadLine() ?? "";
                    string line;
                    while (!string.IsNullOrEmpty(line = reader.ReadLine())) { }

                    string[] parts = requestLine.Split(' ');
                    string target = parts.Length > 1 ? parts[1] : "/";
                    string path = target.Split('?')[0];
                    var query = ParseQuery(target);

                    if (path == "/ping")
                    {
                        Send(stream, 200, "text/plain", Encoding.UTF8.GetBytes($"ok tizen={Capture.TizenVersion}\n"));
                    }
                    else if (path == "/screenshot")
                    {
                        int w = Clamp(Get(query, "w", 960), 64, 3840);
                        int h = Clamp(Get(query, "h", 540), 36, 2160);
                        w -= w % 2; h -= h % 2;
                        query.TryGetValue("fn", out string fn);
                        var (res, info, y, uv) = Capture.GrabFn(fn ?? "screen", w, h, Get(query, "cx", 0), Get(query, "cy", 0),
                                                                Get(query, "cw", 0), Get(query, "ch", 0));
                        if (res < 0)
                        {
                            string why = res == -4 ? "DRM-protected content on screen" :
                                         res == -2 ? "scaler failure (cold-reboot the TV if persistent)" : "capture failed";
                            Send(stream, 503, "text/plain", Encoding.UTF8.GetBytes($"error {res}: {why} | {Capture.LastRaw}\n"));
                            return;
                        }
                        var body = new byte[y.Length + uv.Length];
                        Buffer.BlockCopy(y, 0, body, 0, y.Length);
                        Buffer.BlockCopy(uv, 0, body, y.Length, uv.Length);
                        string extra = $"X-Width: {info.iWidth}\r\nX-Height: {info.iHeight}\r\nX-Format: {info.iRetColorFormat}\r\n" +
                                       $"X-YSize: {y.Length}\r\nX-UVSize: {uv.Length}\r\nX-Result: {res}\r\nX-Raw: {Capture.LastRaw}\r\n";
                        Send(stream, 200, "application/octet-stream", body, extra);
                    }
                    else if (path == "/dbg")
                    {
                        Send(stream, 200, "text/plain", Encoding.UTF8.GetBytes(Capture2.Debug()));
                    }
                    else if (path == "/cap2")
                    {
                        query.TryGetValue("m", out string m);
                        int w = Clamp(Get(query, "w", 960), 64, 7680), h = Clamp(Get(query, "h", 540), 36, 4320);
                        w -= w % 2; h -= h % 2;
                        var r = Capture2.Grab(m ?? "getScreenPostYUV", w, h, Get(query, "cx", 0), Get(query, "cy", 0),
                                              Get(query, "cw", 0), Get(query, "ch", 0));
                        if (r.y == null) { Send(stream, 503, "text/plain", Encoding.UTF8.GetBytes(r.dbg + "\n")); return; }
                        var body = new byte[r.y.Length + r.c.Length];
                        Buffer.BlockCopy(r.y, 0, body, 0, r.y.Length);
                        Buffer.BlockCopy(r.c, 0, body, r.y.Length, r.c.Length);
                        Send(stream, 200, "application/octet-stream", body,
                             $"X-Width: {r.w}\r\nX-Height: {r.h}\r\nX-Format: 0\r\nX-YSize: {r.y.Length}\r\nX-UVSize: {r.c.Length}\r\nX-Raw: {r.dbg}\r\n");
                    }
                    else
                    {
                        Send(stream, 404, "text/plain", Encoding.UTF8.GetBytes("try /ping or /screenshot?w=960&h=540\n"));
                    }
                }
                catch (Exception ex)
                {
                    Tizen.Log.Error("TVShot", "handle: " + ex);
                    try { Send(client.GetStream(), 500, "text/plain", Encoding.UTF8.GetBytes("stage=" + Capture.Stage + "\n" + ex)); } catch { }
                }
            }
        }

        private static void Send(Stream s, int code, string type, byte[] body, string extraHeaders = "")
        {
            string status = code == 200 ? "OK" : code == 404 ? "Not Found" : code == 503 ? "Unavailable" : "Error";
            string head = $"HTTP/1.1 {code} {status}\r\nContent-Type: {type}\r\nContent-Length: {body.Length}\r\n" +
                          $"Access-Control-Allow-Origin: *\r\nConnection: close\r\n{extraHeaders}\r\n";
            var hb = Encoding.ASCII.GetBytes(head);
            s.Write(hb, 0, hb.Length);
            s.Write(body, 0, body.Length);
            s.Flush();
        }

        private static System.Collections.Generic.Dictionary<string, string> ParseQuery(string target)
        {
            var d = new System.Collections.Generic.Dictionary<string, string>();
            int q = target.IndexOf('?');
            if (q < 0) return d;
            foreach (var kv in target.Substring(q + 1).Split('&'))
            {
                var p = kv.Split('=');
                if (p.Length == 2) d[p[0]] = Uri.UnescapeDataString(p[1]);
            }
            return d;
        }

        private static int Get(System.Collections.Generic.Dictionary<string, string> q, string k, int def)
            => q.TryGetValue(k, out var v) && int.TryParse(v, out int n) ? n : def;

        private static int Clamp(int v, int lo, int hi) => Math.Max(lo, Math.Min(hi, v));
    }

    class App : ServiceApplication
    {
        protected override void OnCreate()
        {
            base.OnCreate();
            new Thread(HttpServer.Run) { IsBackground = true, Name = "tvshot-http" }.Start();
        }

        static void Main(string[] args)
        {
            new App().Run(args);
        }
    }
}
