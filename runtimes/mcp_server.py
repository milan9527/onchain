import json

import boto3
from mcp.server.fastmcp import FastMCP

from onchain.config import settings
from onchain.security import TokenService, Denied, digest
from runtimes.isolation import SessionBinding

mcp = FastMCP("Onchain controlled tools", host="0.0.0.0", port=8000, stateless_http=True)
binding = SessionBinding()


def dispatch(tool, arguments, security_context):
    config = settings()
    tokens = TokenService(config)
    claims = tokens.verify(security_context, "onchain-tools")
    binding.check(claims)
    if claims.get("tool") != tool or claims.get("arguments_hash") != digest(arguments):
        raise Denied("ARGUMENTS_CHANGED", "Tool capability does not match arguments.")
    # Only the trusted executor can access account data. This runtime has InvokeFunction permission only.
    result = boto3.client("lambda", region_name=config.region).invoke(
        FunctionName=config.tool_executor_arn,
        Payload=json.dumps({"tool": tool, "arguments": arguments, "security_context": security_context}),
    )
    body = json.loads(result["Payload"].read())
    if result.get("FunctionError"):
        raise RuntimeError("Execution service unavailable.")
    if not body.get("ok"):
        return {"error": body.get("error", {"code": "EXECUTION_FAILED"})}
    return body["result"]


@mcp.tool()
def get_portfolio(security_context: str = "") -> dict:
    """Read the authenticated user's paper portfolio."""
    return dispatch("get_portfolio", {}, security_context)


@mcp.tool()
def get_history(security_context: str = "") -> dict:
    """Read only the authenticated user's operations."""
    return dispatch("get_history", {}, security_context)


@mcp.tool()
def get_quote(symbol: str, security_context: str = "") -> dict:
    """Get an ETH quote, labeled as live or a paper reference."""
    return dispatch("get_quote", {"symbol": symbol}, security_context)


@mcp.tool()
def prepare_trade(amount: str, buy_asset: str, chain: str, max_slippage_bps: int,
                  security_context: str = "") -> dict:
    """Prepare an immutable operation. User confirmation is required before execution."""
    return dispatch("prepare_trade", {
        "amount": amount, "buy_asset": buy_asset, "chain": chain, "max_slippage_bps": max_slippage_bps,
    }, security_context)


@mcp.tool()
def execute_trade(operation_id: str, security_context: str = "") -> dict:
    """Execute a specifically approved paper trade, with atomic budget and replay checks."""
    return dispatch("execute_trade", {"operation_id": operation_id}, security_context)


@mcp.tool()
def get_operation(operation_id: str, security_context: str = "") -> dict:
    """Get an operation only if it belongs to the authenticated user and agent."""
    return dispatch("get_operation", {"operation_id": operation_id}, security_context)


@mcp.tool()
def search_tokens(chain: str, query: str, security_context: str = "") -> dict:
    """Search the chain data service token metadata on a specified network. Read only."""
    return dispatch("search_tokens", {"chain": chain, "query": query}, security_context)


@mcp.tool()
def get_token_info(chain: str, token_address: str, security_context: str = "") -> dict:
    """Read token identity and decimals from the chain data service."""
    return dispatch("get_token_info", {"chain": chain, "token_address": token_address}, security_context)


@mcp.tool()
def get_token_price(chain: str, token_address: str, security_context: str = "") -> dict:
    """Read a live token price; missing data is never replaced with an invented price."""
    return dispatch("get_token_price", {"chain": chain, "token_address": token_address}, security_context)


@mcp.tool()
def get_candles(chain: str, token_address: str, bar: str, limit: int, security_context: str = "") -> dict:
    """Read a bounded set of the chain data service candlesticks."""
    return dispatch("get_candles", {"chain": chain, "token_address": token_address, "bar": bar, "limit": limit}, security_context)


@mcp.tool()
def get_token_holders(chain: str, token_address: str, limit: int, security_context: str = "") -> dict:
    """Read public token holder distribution, not the platform's private user accounts."""
    return dispatch("get_token_holders", {"chain": chain, "token_address": token_address, "limit": limit}, security_context)


@mcp.tool()
def scan_token_risk(chain: str, token_address: str, security_context: str = "") -> dict:
    """Read a provider token risk report. Missing or partial data is not proof of safety."""
    return dispatch("scan_token_risk", {"chain": chain, "token_address": token_address}, security_context)


@mcp.tool()
def get_smart_money_signals(chain: str, limit: int, security_context: str = "") -> dict:
    """Read smart money signals for research; grants no copy-trading authority."""
    return dispatch("get_smart_money_signals", {"chain": chain, "limit": limit}, security_context)


@mcp.tool()
def get_swap_quote(chain: str, from_token: str, to_token: str, amount_raw: str, security_context: str = "") -> dict:
    """Read an aggregator quote with amount in token base units. Does not construct or sign a transaction."""
    return dispatch("get_swap_quote", {"chain": chain, "from_token": from_token, "to_token": to_token,
                                       "amount_raw": amount_raw}, security_context)


@mcp.tool()
def get_gas_price(chain: str, security_context: str = "") -> dict:
    """Read current the chain data service gas prices for a network."""
    return dispatch("get_gas_price", {"chain": chain}, security_context)


@mcp.tool()
def get_supported_chains(security_context: str = "") -> dict:
    """Read networks supported by the the chain data service transaction gateway."""
    return dispatch("get_supported_chains", {}, security_context)


@mcp.tool()
def analyze_portfolio(security_context: str = "") -> dict:
    """Analyze only the authenticated user's simulated holdings, trades and budgets."""
    return dispatch("analyze_portfolio", {}, security_context)


@mcp.tool()
def preview_trade(amount: str, security_context: str = "") -> dict:
    """Check own balance and Agent limits for a USDC amount; never creates approval or executes."""
    return dispatch("preview_trade", {"amount": amount}, security_context)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
