"""Synthetic, deterministic Onchain OS scenarios. No network, wallet or credential access."""
from decimal import Decimal, localcontext

from .extensions import CHAINS
from .security import Denied

DATASET = "onchain-demo-v1"
EPOCH = 1735689600  # Fixed scenario date, never presented as a current observation.
NOTICE = "平台演示数据，非 OKX 返回、非实时行情；风险结果仅演示流程，不能证明真实合约安全。"


def tokens(chain):
    """Base uses familiar identifiers; other networks use explicitly synthetic identifiers."""
    if chain == "8453":
        eth, usdc, risk = ("0x4200000000000000000000000000000000000006",
                           "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
                           "0x000000000000000000000000000000000000dead")
    elif chain == "501":
        eth, usdc, risk = ("So11111111111111111111111111111111111111112",
                           "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "1" * 32)
    else:
        eth, usdc, risk = ("0x" + "0" * 39 + "1", "0x" + "0" * 39 + "2", "0x" + "0" * 36 + "dead")
    return [
        {"chainIndex": chain, "tokenContractAddress": eth, "tokenSymbol": "DEMO-SOL" if chain == "501" else "DEMO-ETH",
         "tokenName": "演示原生资产（合成数据）", "decimals": 9 if chain == "501" else 18,
         "price": "150" if chain == "501" else "3000", "scenario": "reference"},
        {"chainIndex": chain, "tokenContractAddress": usdc, "tokenSymbol": "DEMO-USDC",
         "tokenName": "演示稳定币（合成数据）", "decimals": 6, "price": "1", "scenario": "reference"},
        {"chainIndex": chain, "tokenContractAddress": risk, "tokenSymbol": "DEMO-RISK",
         "tokenName": "高风险演示样本", "decimals": 18, "price": "0.01", "scenario": "high-risk"},
    ]


def find_token(chain, value):
    # Solana identifiers are case-sensitive.
    normalize = (lambda x: x) if chain == "501" else str.lower
    return next((t for t in tokens(chain) if normalize(t["tokenContractAddress"]) == normalize(value)), None)


def example_parameters(template_id, chain="8453"):
    native, usdc, _risk = tokens(chain)
    params = {"chain": chain}
    if template_id == "token-discovery":
        return {**params, "query": native["tokenSymbol"]}
    if template_id in {"token-research", "token-risk", "candle-analysis"}:
        params["token_address"] = native["tokenContractAddress"]
    if template_id == "candle-analysis":
        params.update(bar="1H", limit=10)
    if template_id == "smart-money":
        params["limit"] = 5
    if template_id == "swap-route":
        params.update(from_token=usdc["tokenContractAddress"], to_token=native["tokenContractAddress"],
                      amount_raw="20000000")
    return {} if template_id == "chain-discovery" else params


def invoke(tool, args):
    chain = args.get("chain", "8453")
    asset = find_token(chain, args["token_address"]) if "token_address" in args else None
    pair = {"chainIndex": chain, "tokenContractAddress": args.get("token_address")}
    if tool == "search_tokens":
        query = args["query"].casefold()
        data = [t for t in tokens(chain) if any(query in str(t[k]).casefold()
                for k in ("tokenSymbol", "tokenName", "tokenContractAddress"))]
    elif tool == "get_token_info":
        data = [asset] if asset else [{**pair, "status": "UNKNOWN", "notice": "该合约不在演示数据集中。"}]
    elif tool == "get_token_price":
        data = [{**pair, "price": asset["price"], "time": EPOCH * 1000}] if asset else []
    elif tool == "get_candles":
        seconds = {"5m": 300, "15m": 900, "1H": 3600, "4H": 14400, "1D": 86400}[args["bar"]]
        data = []
        if asset:
            price = Decimal(asset["price"])
            for index in range(args["limit"]):
                # The most recent close matches the price fixture; all candles are completed.
                close = price * (Decimal(1) - Decimal(index) / 1000)
                data.append([str((EPOCH - index * seconds) * 1000), str(close * Decimal(".999")),
                             str(close * Decimal("1.001")), str(close * Decimal(".998")),
                             str(close), "100", str(close * 100), "1"])
    elif tool == "get_token_holders":
        data = [{"label": f"演示持有人 {i+1}", "holdingPercent": str(20 - i),
                 "identity": "synthetic-holder-" + str(i+1)} for i in range(min(args["limit"], 5))] if asset else []
    elif tool == "scan_token_risk":
        high = bool(asset and asset["scenario"] == "high-risk")
        data = [{**pair, "riskLevel": "HIGH" if high else "UNKNOWN", "safeToTrade": False,
                 "scenario": asset["scenario"] if asset else "unknown",
                 "findings": ["演示：卖出受限", "演示：高持仓集中度"] if high else [],
                 "notice": "仅为风险提示演示；未进行真实合约安全检查。"}]
    elif tool == "get_smart_money_signals":
        data = [{"chainIndex": chain, "tokenContractAddress": tokens(chain)[0]["tokenContractAddress"],
                 "signalType": "buy" if i % 2 == 0 else "sell", "amountUsd": str(1000 + 100 * i),
                 "timestamp": EPOCH - 300 * i, "scenario": "synthetic-signal"}
                for i in range(min(args["limit"], 5))]
    elif tool == "get_swap_quote":
        source, target = find_token(chain, args["from_token"]), find_token(chain, args["to_token"])
        if not source or not target or source["scenario"] == "high-risk" or target["scenario"] == "high-risk":
            data = {"status": "NO_ROUTE", "notice": "仅为演示数据集内的参考资产提供模拟报价。"}
        else:
            with localcontext() as ctx:
                ctx.prec = 90
                amount = Decimal(args["amount_raw"]) / (Decimal(10) ** source["decimals"])
                output = amount * Decimal(source["price"]) / Decimal(target["price"])
                units = int(output * (Decimal(10) ** target["decimals"]))
            data = {"chainIndex": chain, "fromToken": source, "toToken": target,
                    "fromTokenAmount": args["amount_raw"], "toTokenAmount": str(units),
                    "route": [{"name": "演示兑换池", "percent": "100"}], "feeUsd": "0",
                    "executable": False, "notice": "演示参考价，与模拟账户一致；不是可提交的链上交易。"}
    elif tool == "get_gas_price":
        data = [{"chainIndex": chain, "unit": "lamports" if chain == "501" else "gwei",
                 "normal": "5000" if chain == "501" else "0.02", "scenario": "synthetic-gas"}]
    elif tool == "get_supported_chains":
        data = [{"chainIndex": key, "chainName": name, "scope": "demo-dataset"} for key, name in CHAINS.items()]
    else:
        raise Denied("TOOL_DENIED", "No reviewed demo adapter for this tool.")
    return {"provider": "Onchain Control Demo", "source": "平台演示数据 / " + DATASET,
            "data_mode": "demo", "dataset": DATASET, "scenario_at": EPOCH,
            "simulated": True, "read_only": True, "data": data, "notice": NOTICE}
