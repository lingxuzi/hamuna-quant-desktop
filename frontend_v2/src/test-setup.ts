// Node 22+ 自带一个实验性的 globalThis.localStorage, 它不是完整的 Storage 实现
// (没有 clear()); vitest 的 jsdom 环境里 window.localStorage 拿到的也是同一个残缺对象,
// 于是所有用到 localStorage 的用例在 beforeEach 就炸了。
// 测试只需要一个按 key 存取的内存实现, 不需要真持久化 —— 装一个最小 Storage 顶上。
function memoryStorage(): Storage {
  const map = new Map<string, string>()
  return {
    get length() { return map.size },
    clear: () => { map.clear() },
    getItem: (k: string) => (map.has(k) ? map.get(k)! : null),
    key: (i: number) => Array.from(map.keys())[i] ?? null,
    removeItem: (k: string) => { map.delete(k) },
    setItem: (k: string, v: string) => { map.set(String(k), String(v)) },
  } as Storage
}

for (const name of ['localStorage', 'sessionStorage'] as const) {
  const current = (globalThis as Record<string, unknown>)[name]
  if (typeof current === 'object' && current !== null && typeof (current as Storage).clear === 'function') continue
  const shim = memoryStorage()
  Object.defineProperty(globalThis, name, { value: shim, configurable: true, writable: true })
  if (typeof window !== 'undefined') {
    Object.defineProperty(window, name, { value: shim, configurable: true, writable: true })
  }
}
