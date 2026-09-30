# 文档与图表维护约定

> Type: Reference
> Status: Active
> Scope: 文档导航、主题归属、状态标记与 PlantUML 图表的维护和渲染

## 文档组织

根目录 [README](../README.md#完整文档导航) 是唯一完整导航。`docs/` 保存详细文档；目录入口只指向 README，不重复维护索引。专题之间可以链接相关说明。

- 一份文档负责一个主题，详细事实在对应专题维护，其他页面引用该专题。
- 内容文件使用小写 `kebab-case.md`；`Repo_Current_State.md` 保留既有名称。
- 每份内容文档只有一个一级标题，标题下声明 `Type`、`Status` 和 `Scope`。
- `Type` 使用 `Architecture`、`Guide`、`Runbook`、`Reference` 或 `State`；`Status` 使用 `Active`、`Deprecated` 或 `Superseded`。
- 历史部署和已停用方案标记状态，并链接现行说明；历史验收记录不能当作当前线上验收结果。
- 重命名文件时同步修改入站链接；新增文档时在 README 添加入口。
- 当前仓库状态由 `Repo_Current_State.md` 记录，内容文档中的日期仅表示对应事件发生时间。

## 图表组织

架构、拓扑、流程和时序图统一采用 PlantUML，不在 Markdown 内维护 Mermaid 或字符拓扑。命令、配置、目录清单、日志样例和普通表格保留原格式。

- 源文件为 `diagrams/<topic>.puml`，预览为同目录同名 `.svg`。
- 根专题图表位于 `docs/diagrams/`；运维日报专题图表位于 `docs/ops-reporting/diagrams/`。
- 所有源文件引用 [统一样式](diagrams/style.iuml)，使用 `plain` 主题和支持中文的 `Noto Sans CJK SC` 字体。
- 组件边界使用组件图，操作流程使用活动图，状态变化使用状态图，交互顺序使用时序图。
- 在所属文档中嵌入 SVG，并在相邻位置链接 `.puml` 源文件。
- 修改源文件后重新渲染 SVG，不手工修改图片；不得在图中加入真实密钥、令牌或客户身份。

## 本地渲染

需要 Java、Graphviz（`dot`）、Noto CJK 字体和 PlantUML JAR。本次图表使用 PlantUML `1.2025.10` 渲染；可从该版本的官方发布页取得 JAR，在本机处理源文件。

在仓库根目录运行，按实际位置替换 JAR 路径：

```bash
java -Djava.awt.headless=true -jar /path/to/plantuml-1.2025.10.jar \
  -charset UTF-8 -checkonly 'docs/diagrams/*.puml' 'docs/ops-reporting/diagrams/*.puml'
java -Djava.awt.headless=true -jar /path/to/plantuml-1.2025.10.jar \
  -charset UTF-8 -tsvg 'docs/diagrams/*.puml' 'docs/ops-reporting/diagrams/*.puml'
```

完成后检查渲染退出状态、SVG 内容和所有图表引用，并检查图片中文字、箭头和组件边界是否清晰。
