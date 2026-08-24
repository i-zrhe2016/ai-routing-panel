# Reality dest 修复 + 多端口(最终状态)

状态:**已回退到多端口架构;保留了 Reality dest 修复。生产数据面真实数据验证通过。**

## 拓扑(务必记住)
- 控制面/面板:`143.198.234.31`(DigitalOcean),容器 `xray-routing-panel`
- 生产数据面:`64.186.224.96`(DMIT 独立公网 IP),容器 `xray-reality-local`
- 面板通过 Tailscale **SSH**(`root@100.65.108.93:22`,key `/run/secrets/fleet_ssh_key`)
  同步本地渲染的 `runtime/config.json` 到远端同路径并 `docker restart` 远端容器。
- 控制面上也有一个同名 `xray-reality-local` 本地容器,**那不是生产**,别搞混。

## 真正的病根 + 唯一保留的修复
**Reality `dest` 原为 `www.microsoft.com`,与 Reality 握手机制不兼容 → 所有真实
客户端握手失败(`handshake did not complete successfully`),表现为"一直超时"。**
TCP 通、openssl 能拿到证书都具迷惑性,连数据面本机 localhost 都失败,确认是纯
服务端问题。**修复:`dest/serverName` 改 `www.amazon.com`**(实测立刻 HTTP 200)。
⚠️ 永远别用 `www.microsoft.com` 做 Reality dest。

## 架构:多端口(已回退)
- 一端口一 inbound(`panel-{port}`),所有端口**共享 UUID**(`XRAY_CLIENT_UUID`)
- 订阅 = `server:{listen_port}`,流量按 `inbound>>>panel-{port}` 统计
- 曾短暂上线过"443 单 inbound + 按用户派生 UUID/统计",**已按要求回退**;
  Reality 密钥也已还原为原值。相对原始配置,**唯一改动就是 dest→amazon**。

## 客户影响
- 端口/UUID/密钥都与原来一致,**只有 SNI 从 microsoft 变成 amazon**。
  自动更新订阅(token 不变)的客户端无感即可用;手动导入的重新导入一次。

## 验证记录
- 远端 6 个端口(30005/30006/30088/31000/31098/31666)全部监听
- 面板订阅(port 31098)= `vless://f0f34f99…@64.186.224.96:31098?…sni=www.amazon.com`
- 真实数据:端口 31098 共享 UUID 客户端经公网 → HTTP 200,出口 147.81.120.142
- 全量 70 测试通过

## 回滚物料
- 远端旧配置备份:`/root/xray-routing-panel/app/xray/runtime/config.json.bak-pre443`
  （那是 microsoft dest 的坏配置,仅应急参考,别真用）
