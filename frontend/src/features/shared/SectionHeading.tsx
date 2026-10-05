import type { ReactNode } from "react";
export function SectionHeading({ eyebrow, title, copy, action }: { eyebrow:string; title:string; copy?:string; action?:ReactNode }) { return <header className="section-heading"><div><p className="eyebrow">{eyebrow}</p><h1>{title}</h1>{copy && <p className="section-copy">{copy}</p>}</div>{action}</header>; }
