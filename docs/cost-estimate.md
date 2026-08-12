# OpenCTI on AWS — 運用コスト概算（Core + Connector）

- **リージョン**: us-west-1（北カリフォルニア）— 実際にデプロイ検証したリージョン
- **料金体系**: オンデマンド（Reserved Instance/Savings Plans未適用）
- **為替レート**: 1 USD = ¥164（2026-07-26時点の実勢レート）
- **算出方法**: AWS Price List API（`pricing.us-east-1.amazonaws.com`、認証不要の公開エンドポイント）から `us-west-1` の実価格を直接取得

> このドキュメントは概算です。特にCloudWatch Logs取込量・S3ストレージ量・NAT経由データ量は運用状況に応じて大きく変動します（詳細は [前提・変動要因](#前提変動要因重要) を参照）。

## Coreスタックの月額固定費（内訳）

| 項目 | 単価 | 月額（730h換算） |
|---|---|---|
| NAT Gateway（時間分） | $0.048/hr | $35.04 |
| SSM中継 EC2 (t4g.micro) | $0.01/hr | $7.30 |
| SSM中継 EBS gp3 8GB | $0.096/GB-mo | $0.77 |
| ECS Fargate Platform (2vCPU/16GB ×1) | $0.04656/vCPU-hr + $0.00511/GB-hr | $127.66 |
| ECS Fargate Worker (1vCPU/2GB ×2) | 同上 | $82.90 |
| OpenSearch r7g.large.search ×1 | $0.198/hr | $144.54 |
| OpenSearch gp3ストレージ 300GB | $0.1464/GB-mo | $43.92 |
| OpenSearch gp3追加スループット（250-125=125MiB/s） | $0.0768/MiBps-mo | $9.60 |
| ElastiCache Redis cache.r7g.large ×1 | $0.219/hr | $159.87 |
| Amazon MQ mq.m7g.medium (Single-AZ) | $0.1595/hr | $116.44 |
| Amazon MQストレージ（20GB想定） | $0.12/GB-mo | $2.40 |
| Secrets Manager（Admin/暗号鍵/Health/Redis/MQの5個） | $0.40/secret | $2.00 |
| CloudWatch Logs（軽負荷想定：2GB/月） | $0.67/GB取込 | $1.34 |
| S3 Live/Archive（軽負荷想定：数GB） | $0.026/GB | $0.13 |
| NATデータ処理（軽負荷想定：20GB/月） | $0.048/GB | $0.96 |
| **Core合計** | | **$734.86/月** |

## Connector 1個あたりの月額

| 項目 | 単価 | 月額 |
|---|---|---|
| ECS Fargate Connector Task (0.5vCPU/1GB ×1) | $0.04656/vCPU-hr + $0.00511/GB-hr | $20.72 |
| Secrets Manager（Connector Token） | $0.40/secret | $0.40 |
| CloudWatch Logs（軽負荷想定：0.5GB/月） | $0.67/GB取込 | $0.34 |
| NATデータ処理（軽負荷想定：2GB/月） | $0.048/GB | $0.10 |
| **Connector1個合計** | | **$21.56/月** |

## まとめ：Core + Connector×0〜5

| 構成 | 月額(USD) | 月額(円) | 年額(USD) | 年額(円) |
|---|---:|---:|---:|---:|
| Coreのみ | $734.86 | **¥120,518** | $8,818.37 | **¥1,446,213** |
| Core + Connector×1 | $756.42 | **¥124,053** | $9,077.04 | **¥1,488,634** |
| Core + Connector×2 | $777.98 | **¥127,588** | $9,335.71 | **¥1,531,056** |
| Core + Connector×3 | $799.53 | **¥131,123** | $9,594.38 | **¥1,573,478** |
| Core + Connector×4 | $821.09 | **¥134,658** | $9,853.04 | **¥1,615,899** |
| Core + Connector×5 | $842.64 | **¥138,193** | $10,111.71 | **¥1,658,321** |

## 内訳の傾向

固定費の大半（$734.86のうち約$728＝99%）は**5つの常時稼働マネージドサービス**が占めます。

| サービス | 月額 | 構成比 |
|---|---:|---:|
| ElastiCache Redis | $159.87 | 21.8% |
| OpenSearch（compute+storage+throughput） | $198.06 | 27.0% |
| ECS Fargate（Platform+Worker） | $210.56 | 28.7% |
| Amazon MQ（compute+storage） | $118.84 | 16.2% |
| NAT Gateway | $35.04 | 4.8% |
| その他（SSM中継/Secrets/Logs/S3） | $12.49 | 1.7% |

Connectorは1個あたり月$21.56（約¥3,536）程度と軽く、5個追加しても全体への影響は約+15%です。

## 前提・変動要因（重要）

- **軽負荷想定とした3項目**（CloudWatch Logs取込量、S3ストレージ量、NAT経由データ量）は運用次第で大きく変動します。特に外部Feed Connector（MITRE/CISA等、常時ポーリング）を複数追加すると、ログ量・NAT通信量が増えて月数千〜数万円上振れする可能性があります
- **長期運用でのストレージ増加は含みません**（OpenSearch/S3は実データが蓄積すると容量課金が増えます。1年後は300GBの前提を超える可能性が高いです）
- **Reserved Instance/Savings Plansなど割引契約は未適用**（オンデマンドのみ）。OpenSearch/Redisは1年前約で3〜4割程度下がる余地があります
- データ転送量（アウトバウンド、SSM経由等）は僅少想定
- サポートプラン費用は含みません
- 為替レートは算出時点のスナップショットであり、実際の請求時レートとは異なります

## 参照した料金（AWS Price List API, us-west-1）

| リソース | 単価 | 取得元 |
|---|---|---|
| Fargate x86_64 vCPU | $0.04656/hr | AmazonECS |
| Fargate x86_64 メモリ | $0.00511/GB-hr | AmazonECS |
| NAT Gateway | $0.048/hr + $0.048/GB | AmazonEC2 |
| EC2 t4g.micro | $0.01/hr | AmazonEC2 |
| EBS gp3 | $0.096/GB-mo | AmazonEC2 |
| OpenSearch r7g.large.search | $0.198/hr | AmazonES |
| OpenSearch gp3ストレージ | $0.1464/GB-mo | AmazonES |
| OpenSearch gp3追加スループット | $0.0768/MiBps-mo | AmazonES |
| ElastiCache cache.r7g.large (Redis) | $0.219/hr | AmazonElastiCache |
| Amazon MQ mq.m7g.medium (RabbitMQ, Single-AZ) | $0.1595/hr | AmazonMQ |
| Amazon MQ RabbitMQストレージ(EBS) | $0.12/GB-mo | AmazonMQ |
| Secrets Manager | $0.40/secret/月 | AWSSecretsManager |
| CloudWatch Logs取込 | $0.67/GB | AmazonCloudWatch |
| CloudWatch Logsストレージ | $0.033/GB-mo | AmazonCloudWatch |
| S3標準ストレージ | $0.026/GB-mo（先頭50TB） | AmazonS3 |
