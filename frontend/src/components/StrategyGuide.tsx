import { BookOpen, Clock3, Database, ListTree, ShieldAlert } from "lucide-react";
import { marked, type Renderer, type Tokens } from "marked";

import markdown from "../content/volume_price_analysis.md?raw";

interface GuideSection {
  id: string;
  label: string;
}

interface MethodSummary {
  name: string;
  englishName: string;
  focus: string;
  description: string;
  tone: "lime" | "blue" | "gold" | "red";
}

const METHOD_SUMMARIES: MethodSummary[] = [
  {
    name: "VSA",
    englishName: "Volume Spread Analysis",
    focus: "逐 K 线",
    description: "把成交量、波动范围和收盘位置放在同一根 K 线上观察。",
    tone: "lime",
  },
  {
    name: "Wyckoff",
    englishName: "Market Structure",
    focus: "看阶段",
    description: "用供需和区间事件解释累积、上涨、派发与下跌。",
    tone: "blue",
  },
  {
    name: "VPA",
    englishName: "Volume Price Analysis",
    focus: "看配合",
    description: "用更宽泛的语言比较价格结果和成交量变化是否一致。",
    tone: "gold",
  },
  {
    name: "Volume Profile",
    englishName: "Price Distribution",
    focus: "看价位",
    description: "沿价格轴统计成交量，定位 POC、价值区和密集节点。",
    tone: "red",
  },
];

function cleanHeadingText(value: string): string {
  return value
    .replace(/<[^>]*>/g, "")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/[\*_~`]/g, "")
    .trim();
}

function slugify(value: string): string {
  const slug = cleanHeadingText(value)
    .normalize("NFKC")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, "-")
    .replace(/^-+|-+$/g, "");
  return slug || "section";
}

function uniqueSlug(value: string, seen: Map<string, number>): string {
  const base = slugify(value);
  const count = seen.get(base) ?? 0;
  seen.set(base, count + 1);
  return count === 0 ? base : `${base}-${count + 1}`;
}

function buildSections(): GuideSection[] {
  const seen = new Map<string, number>();
  return marked
    .lexer(markdown)
    .filter((token): token is Tokens.Heading => token.type === "heading" && token.depth === 2)
    .map((token) => ({ id: uniqueSlug(token.text, seen), label: cleanHeadingText(token.text) }));
}

function renderDocument(): string {
  const seen = new Map<string, number>();
  const renderer = new marked.Renderer();
  renderer.heading = function heading(this: Renderer, { tokens, depth }: Tokens.Heading) {
    const content = this.parser.parseInline(tokens);
    const id = uniqueSlug(String(content), seen);
    return `<h${depth} id="${id}">${content}</h${depth}>\n`;
  };
  const renderTable = renderer.table.bind(renderer);
  renderer.table = function table(this: Renderer, token: Tokens.Table) {
    return `<div class="strategy-table-scroll">${renderTable(token)}</div>`;
  };

  // The source is tracked at build time; no user or remote HTML enters this renderer.
  return marked.parse(markdown, { async: false, gfm: true, renderer });
}

const GUIDE_SECTIONS = buildSections();
const DOCUMENT_HTML = renderDocument();

export function StrategyGuide() {
  return (
    <main className="knowledge-workspace" aria-labelledby="knowledge-title">
      <section className="knowledge-intro">
        <div className="knowledge-heading-row">
          <div className="knowledge-heading-copy">
            <p className="eyebrow">METHOD LIBRARY / VOLUME &amp; PRICE</p>
            <h2 id="knowledge-title">量价方法</h2>
            <p>
              把市场阶段、逐 K 线信号和价格分布放进同一套可复核的短线观察流程。
            </p>
          </div>
          <div className="knowledge-meta" aria-label="文档状态">
            <BookOpen size={18} />
            <span>研究导读 · v1</span>
          </div>
        </div>

        <div className="method-summary-grid" aria-label="四种量价方法">
          {METHOD_SUMMARIES.map((method) => (
            <div className={`method-summary method-summary-${method.tone}`} key={method.name}>
              <div className="method-summary-topline">
                <strong>{method.name}</strong>
                <span>{method.focus}</span>
              </div>
              <small>{method.englishName}</small>
              <p>{method.description}</p>
            </div>
          ))}
        </div>

        <div className="knowledge-boundary-strip" aria-label="数据边界">
          <span><Database size={15} /> 当前本地快照：日线</span>
          <span><Clock3 size={15} /> 分钟与 Tick 需要独立数据源</span>
          <span><ShieldAlert size={15} /> 所有形态都需要后续确认</span>
        </div>
      </section>

      <div className="knowledge-layout">
        <aside className="knowledge-toc" aria-label="文档章节">
          <div className="knowledge-toc-title"><ListTree size={15} /> 章节</div>
          <nav>
            {GUIDE_SECTIONS.map((section) => (
              <a href={`#${section.id}`} key={section.id}>
                {section.label}
              </a>
            ))}
          </nav>
        </aside>

        <article
          className="strategy-document"
          lang="zh-CN"
          dangerouslySetInnerHTML={{ __html: DOCUMENT_HTML }}
        />
      </div>
    </main>
  );
}
