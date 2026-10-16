// 中文词条：键即文案本身。
//
// 这里刻意不逐条写死「键: 同样的中文」，而是从英文词条表的键推导出来——
// 新增文案只改 en-US.ts 一处，避免两边漏同步导致切到中文时露出键名。
import enUS from './en-US'

export default Object.fromEntries(Object.keys(enUS).map((key) => [key, key])) as Record<
  string,
  string
>
