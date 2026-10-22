# Set the default audio endpoint (render or capture) by device friendly-name substring.
# Usage:  .\set-default-audio.ps1 -Name "CABLE Output" -Kind capture
#         .\set-default-audio.ps1 -List
param(
    [string]$Name,
    [ValidateSet('render', 'capture')]
    [string]$Kind = 'capture',
    [switch]$List,
    [switch]$Get
)

$code = @'
using System;
using System.Runtime.InteropServices;

namespace AudioPolicy
{
    [ComImport, Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMMDeviceEnumerator
    {
        int EnumAudioEndpoints(int dataFlow, int stateMask, out IntPtr devices);
        int GetDefaultAudioEndpoint(int dataFlow, int role, out IntPtr device);
        int GetDevice(string id, out IntPtr device);
        int RegisterEndpointNotificationCallback(IntPtr client);
        int UnregisterEndpointNotificationCallback(IntPtr client);
    }

    [ComImport, Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMMDevice
    {
        int Activate(ref Guid iid, int clsCtx, IntPtr activationParams, [MarshalAs(UnmanagedType.IUnknown)] out object iface);
        int OpenPropertyStore(int access, out IntPtr properties);
        int GetId([MarshalAs(UnmanagedType.LPWStr)] out string id);
        int GetState(out int state);
    }

    [ComImport, Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IPropertyStore
    {
        int GetCount(out int count);
        int GetAt(int index, out PROPERTYKEY key);
        int GetValue(ref PROPERTYKEY key, out PROPVARIANT value);
        int SetValue(ref PROPERTYKEY key, ref PROPVARIANT value);
        int Commit();
    }

    [StructLayout(LayoutKind.Sequential)]
    struct PROPERTYKEY { public Guid fmtid; public int pid; }

    [StructLayout(LayoutKind.Explicit, Size = 32)]
    struct PROPVARIANT
    {
        [FieldOffset(0)] public short vt;
        [FieldOffset(8)] public IntPtr pointerValue;
    }

    [ComImport, Guid("f8679f50-850a-41cf-9c72-430f290290c8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IPolicyConfig
    {
        int GetMixFormat(string deviceName, IntPtr ppFormat);
        int GetDeviceFormat(string deviceName, bool bDefault, IntPtr ppFormat);
        int ResetDeviceFormat(string deviceName);
        int SetDeviceFormat(string deviceName, IntPtr pEndpointFormat, IntPtr mixFormat);
        int GetProcessingPeriod(string deviceName, bool bDefault, IntPtr pmftDefaultPeriod, IntPtr pmftMinimumPeriod);
        int SetProcessingPeriod(string deviceName, IntPtr pmftPeriod);
        int GetShareMode(string deviceName, IntPtr pMode);
        int SetShareMode(string deviceName, IntPtr mode);
        int GetPropertyValue(string deviceName, bool bFxStore, ref PROPERTYKEY key, ref PROPVARIANT value);
        int SetPropertyValue(string deviceName, bool bFxStore, ref PROPERTYKEY key, ref PROPVARIANT value);
        int SetDefaultEndpoint(string deviceName, int role);
        int SetEndpointVisibility(string deviceName, bool visible);
    }

    public static class Policy
    {
        static readonly Guid CLSID_PolicyConfig = new Guid("870af99c-171d-4f9e-af0d-e63df40c2bc9");
        static readonly Guid IID_IMMDeviceEnumerator = new Guid("A95664D2-9614-4F35-A746-DE8DB63617E6");
        static readonly PROPERTYKEY PKEY_Device_FriendlyName =
            new PROPERTYKEY { fmtid = new Guid("a45c254e-df1c-4efd-8020-67d146a850e0"), pid = 14 };

        public static string[] List(int dataFlow)
        {
            IMMDeviceEnumerator en = (IMMDeviceEnumerator)new MMDeviceEnumerator();
            IntPtr collPtr;
            en.EnumAudioEndpoints(dataFlow, 1, out collPtr);
            IMMDeviceCollection coll = (IMMDeviceCollection)Marshal.GetObjectForIUnknown(collPtr);
            int count;
            coll.GetCount(out count);
            string[] names = new string[count];
            for (int i = 0; i < count; i++)
            {
                IntPtr devPtr;
                coll.Item(i, out devPtr);
                IMMDevice dev = (IMMDevice)Marshal.GetObjectForIUnknown(devPtr);
                names[i] = FriendlyName(dev) + "|" + IdOf(dev);
            }
            return names;
        }

        static string IdOf(IMMDevice dev)
        {
            string id;
            dev.GetId(out id);
            return id;
        }

        static string FriendlyName(IMMDevice dev)
        {
            IntPtr storePtr;
            dev.OpenPropertyStore(0, out storePtr);
            IPropertyStore store = (IPropertyStore)Marshal.GetObjectForIUnknown(storePtr);
            PROPERTYKEY key = PKEY_Device_FriendlyName;
            PROPVARIANT pv;
            store.GetValue(ref key, out pv);
            return Marshal.PtrToStringUni(pv.pointerValue) ?? "";
        }

        public static string DefaultName(int dataFlow)
        {
            IMMDeviceEnumerator en = (IMMDeviceEnumerator)new MMDeviceEnumerator();
            IntPtr devPtr;
            int hr = en.GetDefaultAudioEndpoint(dataFlow, 0, out devPtr);
            if (hr != 0) return "";
            IMMDevice dev = (IMMDevice)Marshal.GetObjectForIUnknown(devPtr);
            return FriendlyName(dev);
        }

        public static bool SetDefault(string match, int dataFlow)
        {
            foreach (string item in List(dataFlow))
            {
                string[] parts = item.Split('|');
                if (parts[0].IndexOf(match, StringComparison.OrdinalIgnoreCase) < 0) continue;
                string id = parts[1];
                IPolicyConfig pc = (IPolicyConfig)Activator.CreateInstance(Type.GetTypeFromCLSID(CLSID_PolicyConfig));
                for (int role = 0; role < 3; role++) pc.SetDefaultEndpoint(id, role);
                return true;
            }
            return false;
        }
    }

    [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
    class MMDeviceEnumerator { }

    [ComImport, Guid("0BD7A1BE-7A1A-44DB-8397-CC5392387B5E"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMMDeviceCollection
    {
        int GetCount(out int count);
        int Item(int index, out IntPtr device);
    }
}
'@

Add-Type -TypeDefinition $code -Language CSharp | Out-Null

if ($Get) {
    'render=' + [AudioPolicy.Policy]::DefaultName(0)
    'capture=' + [AudioPolicy.Policy]::DefaultName(1)
    exit 0
}

if ($List) {
    $flowLabel = @{ render = 'RENDER'; capture = 'CAPTURE' }
    foreach ($k in @('render', 'capture')) {
        $flow = if ($k -eq 'render') { 0 } else { 1 }
        "--- $($flowLabel[$k]) ---"
        [AudioPolicy.Policy]::List($flow) | ForEach-Object { $_.Split('|')[0] }
    }
    exit 0
}

if (-not $Name) { throw 'need -Name (or -List)' }
$flow = if ($Kind -eq 'render') { 0 } else { 1 }
$ok = [AudioPolicy.Policy]::SetDefault($Name, $flow)
if ($ok) { "OK: default $Kind device now matches '$Name'" } else { throw "no $Kind device matched '$Name'" }


