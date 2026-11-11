/// <reference types="vite/client" />

declare module '*.css' {
  const content: string
  export default content
}

// design/full 用自定义元素 <k>/<v> 做无类名键值; React 需登记才有类型
declare namespace JSX {
  interface IntrinsicElements {
    k: React.DetailedHTMLProps<React.HTMLAttributes<HTMLElement>, HTMLElement>
    v: React.DetailedHTMLProps<React.HTMLAttributes<HTMLElement>, HTMLElement>
  }
}
