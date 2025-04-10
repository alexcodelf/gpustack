# Windows平台进程管理指南

## 问题背景

在Windows系统上，当GPUStack主进程(`gpustack.exe`)被用户通过任务管理器强制终止时，由于Windows的进程管理机制与Linux/macOS不同，子进程(如worker进程)可能不会自动终止。这会导致这些子进程继续运行并占用资源，特别是网络端口，从而在下次启动GPUStack时出现冲突，导致新实例无法正常启动。

## 解决方案

从版本X.X.X开始，GPUStack采用了Windows系统的Job对象机制来解决这个问题。Job对象是Windows提供的一种进程组管理机制，能确保当主进程终止时，所有相关的子进程也被自动终止。

### 技术实现

1. 在GPUStack启动时，会创建一个Job对象并将主进程分配给它
2. 设置JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE标志，确保主进程终止时所有子进程一起终止
3. 所有创建的子进程会自动成为Job对象的一部分
4. 即使主进程被强制终止，Windows系统也会自动清理所有子进程

### 依赖要求

此功能依赖于pywin32包，该包会在Windows系统上自动安装。如果您是手动安装GPUStack，请确保安装了此依赖：

```
pip install pywin32
```

## 兼容性说明

- 此功能仅在Windows平台上激活
- 对Linux和macOS用户无影响，这些平台已有内置的进程组管理机制
- 适用于所有Windows进程终止场景，包括任务管理器强制终止和系统关机

## 故障排除

如果在Windows系统上仍然遇到端口占用问题，可尝试以下步骤：

1. 检查是否有旧的GPUStack进程仍在运行：
   ```
   tasklist | findstr gpustack
   ```

2. 手动终止所有相关进程：
   ```
   taskkill /F /IM gpustack.exe
   taskkill /F /IM python.exe /FI "WINDOWTITLE eq gpustack*"
   ```

3. 使用netstat查看谁占用了端口：
   ```
   netstat -ano | findstr 10150
   ```
   (10150是默认worker端口)

4. 如果问题持续，可以尝试更改worker端口：
   ```
   gpustack start --worker-port 10151
   ```

## 最佳实践

- 始终通过GPUStack提供的关闭命令或界面停止服务
- 避免通过任务管理器直接终止`gpustack.exe`进程
- 定期更新GPUStack以获取最新的进程管理改进
