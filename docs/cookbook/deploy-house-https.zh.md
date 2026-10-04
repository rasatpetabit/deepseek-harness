# 准备内部 HTTPS 代理

[English](deploy-house-https.md) | 中文

## 概要

准备仅限 LAN 的 nginx 入口，让浏览器在 `https://192.168.100.200` 自动登录，同时不暴露回环后端，也不修改其持久化签名密钥。本流程准备并测试仓库产物；它不授权安装、证书启用、服务重启或激活。[升级协调决策](../../.agents/notes/proposed/architecture/2026-10-04-upstream-reconciliation.zh.md)负责安全策略及尚未确定的证书启用选择。

## 目录

- [1. 选择参数](#1-select-parameters)
- [2. 验证临时入口](#2-verify-the-scratch-edge)
- [3. 满足激活前提](#3-satisfy-activation-prerequisites)
- [转发参考](#forwarding-reference)

<a id="1-select-parameters"></a>

## 1. 选择参数

使用具有 HTTP TLS 模块及 `proxy_cookie_flags` 支持的 nginx（1.19.3 或更新版本）。逐字替换[模板](../../scripts/deploy/nginx-dsh.conf.template)中的每个 `@PARAMETER@`，然后在专用 nginx `http` 块内包含生成的文件。不要使用不受限的环境变量替换：必须保留 `$http_host` 等 nginx 变量。不要将此配置与其他监听器或继承的 real-IP 配置组合，以免绕过或改变基于套接字地址的 ACL。

| 参数 | 部署值或来源 |
| --- | --- |
| `LISTEN_ADDRESS`, `HTTPS_PORT` | 已批准的公开监听器：`192.168.100.200`、`443` |
| `PUBLIC_AUTHORITY` | `192.168.100.200`，不含协议或端口 |
| `PUBLIC_AUTHORITY_REGEX` | 正则转义后的 authority：`192\.168\.100\.200` |
| `LAN_CIDR` | 已批准的客户端网络：`192.168.100.0/24`；绝不添加回环地址 |
| `BACKEND_ADDRESS`, `BACKEND_PORT` | 现有回环后端：`127.0.0.1`、`3080` |
| `CERTIFICATE_PATH`, `KEY_PATH` | 由操作员证书流程提供的绝对路径 |
| `ACCESS_LOG_PATH` | 由部署配置提供的日志绝对路径 |

参数是受信任的 nginx 配置，而非用户输入。拒绝未替换的占位符。根路径、静态资源、API、流和升级请求必须共用一个服务器级 ACL 和一个转发 location。公开 Host 必须与不含 `:443` 的 authority 完全一致；任何非空 Origin 必须与 HTTPS origin 完全一致。每个升级请求都必须携带该 Origin，且跨站 Fetch Metadata 被拒绝。

<a id="2-verify-the-scratch-edge"></a>

## 2. 验证临时入口

在具有已批准 LAN 本地地址的 Linux 主机上执行此检查，不安装系统软件包或服务。提供现有 nginx 可执行文件，以及 `openssl`、`ip`、Python 和 pytest。归属此脚本的测试创建私有临时前缀、自签名测试证书、高端口 HTTPS 监听器及确定性回环后端；它运行真正的 `nginx -t`，并清理两个服务器。临时证书不能证明浏览器信任。

```sh
DSH_TEST_NGINX=/tmp/dsh-nginx-runtime/usr/sbin/nginx python3 -m pytest -q -s scripts/tests/test_https_proxy.py
```

命令必须报告 nginx 语法验证成功及运行时用例通过。缺少可执行文件、TLS 模块或 LAN 地址时，验证失败而非跳过。`DSH_TEST_LAN_ADDRESS` 可选择现有本地地址；`DSH_TEST_LAN_CIDR` 和 `DSH_TEST_PUBLIC_AUTHORITY` 可用于另一个隔离测试网络，而不改变部署策略。监听器和后端端口动态分配，绝不复制运行中服务的端口。运行时检查涵盖所有路由类别的允许 LAN 与拒绝回环源套接字、浏览器准入、双向升级、请求头移除、cookie 值不变、无查询参数日志，以及后端完成之前的首分片传输。

<a id="3-satisfy-activation-prerequisites"></a>

## 3. 满足激活前提

发布操作员在独立审查精确产物后负责这些检查。本指南记录所需证据，不表示生产部署已完成。

1. 确定证书签发者及续期负责人。验证证书 IP SAN 包含已批准的公开 IP、证书链完整、有效期可接受，且每个目标浏览器无需绕过验证即可信任该链。不要以临时证书成功代替此证据。
2. 验证后端仅绑定 `127.0.0.1:3080`，并拒绝直接 LAN 连接。为预期公开 Host 和 HTTPS origin 配置现有浏览器信任策略，受信任回环地址只代表代理一跳。保留持久化浏览器签名密钥及现有凭据；不要生成新的代理密钥。
3. 在目标隔离运行时配置中使用 `nginx -t` 验证最终生成的配置。使用真实 LAN 和非 LAN 客户端重复允许／拒绝路由检查；确保没有其他监听器、location、继承的 real-IP 指令或 HTTP 回退绕过准入。
4. 从目标受信任浏览器验证干净 `GET /` 自动设置签名 cookie 并重定向到 `./`、重新加载成功，且无 cookie 的 API 请求失败。验证错误 Host／Origin、HTTP／null Origin、跨站 Fetch Metadata、伪造转发请求头及缺少精确 Origin 的升级请求被拒绝。确认后端收到原始且被接受的公开 Host，而非回环 authority。
5. 在 `/srv/dev` 之外保留已审查版本及配套回滚配置，包括一致的数据、插件、设置、凭据、服务配置和入口配置。安装和激活各自需要单独授权；本流程两者都不执行。

<a id="forwarding-reference"></a>

## 转发参考

入口转发明确列出的请求头，而不解析转发身份。它保留 cookie、Origin、Fetch Metadata、授权、内容分帧、浏览器内容协商、条件／范围请求、SSE（Server-Sent Events）恢复和 WebSocket 协议请求头。它丢弃 `Forwarded`、`X-Real-IP`、所有 `X-Forwarded-*` 及其他未列出的请求头；需要其他请求头的扩展必须更新模板和运行时测试。签名 cookie 值保持不变，响应 cookie 添加 Secure、HttpOnly 和 SameSite=Strict。流和请求体不缓冲，代理转发时保留查询字符串。

访问日志记录方法与路径，绝不记录 `$request`、`$request_uri` 或查询参数。服务器丢弃请求错误日志，因为 nginx 错误消息可能包含带查询参数的完整 URI；这以放弃请求级代理诊断换取避免记录登录 token。全局启动诊断在该服务器日志之外，且不得添加含请求信息的日志。nginx 标准代理超时仍然有效；长连接流和升级连接需要后端在超时期限内发送保活流量。
