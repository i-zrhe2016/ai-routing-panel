# Prometheus Targets 与 Labels

> 权威范围：抓取目标、标签基数和查询身份

## Target 设计

每个真实节点和 exporter 端点应有唯一 target。Prometheus 服务发现或静态配置必须保留稳定身份，不以易变 IP 作为报告主键。

必需标签：

| 标签 | 含义 | 示例值 |
| --- | --- | --- |
| `job` | exporter 类型 | `node`、`xray`、`blackbox` |
| `instance` | 实际抓取端点 | `host:port` |
| `node_id` | 稳定、非敏感节点 ID | `normal-01` |
| `node_role` | 节点角色 | `control_plane`、`normal_data_plane`、`ai_data_plane` |
| `environment` | 环境 | `production`、`staging` |
| `region` | 部署区域 | 受控枚举 |

禁止把 UUID、订阅 token、域名、客户端 IP、错误文本或请求路径放入标签。高基数字段既增加存储成本，也可能泄漏业务信息。

## 当前生产 Targets

当前配置共有 8 个 targets：控制面面板、控制面 node-exporter/cAdvisor、普通数据面的 node-exporter/cAdvisor、AI 数据面的 node-exporter/cAdvisor，以及 Prometheus 自身。普通数据面的 node-exporter/cAdvisor 使用控制面可达的 Tailscale 地址 `redacted-ip-003`，避免经公网地址访问被数据面防火墙丢弃。AI 本机备用的 Xray `/debug/vars` 不作为 Prometheus 独立 target，而由面板 `/metrics` 聚合并鉴权后暴露。

生产节点序列必须提供稳定的 `node_id`、`node_role`、`environment` 和 `region`。当前 `node_id` 使用 `control-01`、`normal-01`、`ai-01`，环境为 `production`。权威地域尚未确认时使用受控值 `unknown`，不得猜测机房位置。

## 查询约束

日报查询必须同时限定 `environment`、`node_id` 和预期 `job`，并验证每个节点只有一条期望序列。使用 range query 覆盖完整报告窗口；查询结果保存指标名、标签选择器、起止时间、步长和样本覆盖率，不保存原始日志。

## Target 健康门禁

上线前确认：

1. Prometheus `/targets` 中所有目标为预期地址且抓取成功；
2. `up`、节点资源、服务状态与 blackbox 指标都有稳定样本；
3. 标签集合符合允许列表，无重复 `node_id + job`；
4. 抓取间隔与规则阈值匹配，时钟同步；
5. Prometheus API 只向日报器提供只读访问，并限制网络来源。

缺少必需标签、出现重复序列或 target 超过两个抓取周期不可达时，日报必须标记数据源缺口，而不是猜测节点状态。
