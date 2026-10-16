// WeFlow 图片自动下载服务自检（在 WeFlow 自己的 Electron 运行时里跑）
//
//   set ELECTRON_RUN_AS_NODE=1
//   "<tree>\node_modules\electron\dist\electron.exe" weflow_imgdiag.js
//
// 复现 electron/services/imageDownloadService.ts 的三步：
//   require('koffi') -> getDllPath() 是否存在 -> load + InitImgHelper 是否可用
// 这样能直接看出 hook 失败的真正原因（WeFlow 只把错误丢进 console）。

const fs = require('fs')
const path = require('path')

const tree = process.argv[2] || process.cwd()
console.log('node =', process.versions.node, 'electron =', process.versions.electron || '(none)')
console.log('cwd  =', process.cwd())
console.log('tree =', tree)

let koffi = null
try {
  koffi = require(path.join(tree, 'node_modules', 'koffi'))
  console.log('koffi: OK, version =', koffi.version || '(unknown)')
} catch (e) {
  console.log('koffi: FAILED ->', e && e.message)
}

const candidates = [
  path.join(process.cwd(), 'resources', 'image', 'win32', 'x64', 'img_helper.dll'),
  path.join(tree, 'resources', 'image', 'win32', 'x64', 'img_helper.dll'),
]
for (const p of candidates) {
  console.log(`dll(${p}) exists = ${fs.existsSync(p)}`)
}

if (koffi) {
  const dll = candidates.find((p) => fs.existsSync(p))
  if (!dll) {
    console.log('=> 结论：DLL 路径不存在（getDllPath 会返回候选[0]并让 ensureInitialized 失败）')
  } else {
    try {
      const lib = koffi.load(dll)
      const getErr = lib.func('const char* GetImgHelperError()')
      console.log('koffi.load(dll): OK')
      const raw = getErr()
      console.log('GetImgHelperError() =', JSON.stringify(raw || ''))
    } catch (e) {
      console.log('koffi.load(dll): FAILED ->', e && e.message)
    }
  }
}
