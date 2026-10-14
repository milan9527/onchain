import json

import boto3
from mcp.server.fastmcp import FastMCP

from onchain.config import settings
from onchain.security import Denied, bearer
from onchain.invocations import target_identity
from runtimes.isolation import SessionBinding

mcp = FastMCP("Onchain controlled tools", host="0.0.0.0", port=8000, stateless_http=True)
binding = SessionBinding()


def dispatch(tool, arguments, context_id):
    config = settings()
    request = mcp.get_context().request_context.request
    access_token = bearer(dict(request.headers))
    target_identity(config, access_token)
    payload = {"tool": tool, "arguments": arguments, "context_id": context_id, "access_token": access_token}
    client = boto3.client("lambda", region_name=config.region)
    def invoke(phase):
        response = client.invoke(FunctionName=config.tool_executor_arn,
                                 Payload=json.dumps({**payload, "phase": phase}))
        body = json.loads(response["Payload"].read())
        if response.get("FunctionError"):
            raise Denied("EXECUTOR_UNAVAILABLE", "Execution service unavailable.", 503)
        return body
    authorization = invoke("authorize")
    if not authorization.get("ok"):
        return {"error": authorization.get("error", {"code": "EXECUTION_FAILED"})}
    binding.check(authorization["result"])
    result = invoke("execute")
    if not result.get("ok"):
        return {"error": result.get("error", {"code": "EXECUTION_FAILED"})}
    return result["result"]


@mcp.tool()
def get_portfolio(context_id: str = "") -> dict:
    """Read the authenticated user's paper portfolio."""
    return dispatch("get_portfolio", {}, context_id)


@mcp.tool()
def get_history(context_id: str = "") -> dict:
    """Read only the authenticated user's operations."""
    return dispatch("get_history", {}, context_id)


@mcp.tool()
def get_quote(symbol: str, context_id: str = "") -> dict:
    """Get an ETH quote, labeled as live or a paper reference."""
    return dispatch("get_quote", {"symbol": symbol}, context_id)


@mcp.tool()
def prepare_trade(amount: str, buy_asset: str, chain: str, max_slippage_bps: int,
                  context_id: str = "") -> dict:
    """Prepare an immutable operation. User confirmation is required before execution."""
    return dispatch("prepare_trade", {
        "amount": amount, "buy_asset": buy_asset, "chain": chain, "max_slippage_bps": max_slippage_bps,
    }, context_id)


@mcp.tool()
def execute_trade(operation_id: str, context_id: str = "") -> dict:
    """Execute a specifically approved paper trade, with atomic budget and replay checks."""
    return dispatch("execute_trade", {"operation_id": operation_id}, context_id)


@mcp.tool()
def get_operation(operation_id: str, context_id: str = "") -> dict:
    """Get an operation only if it belongs to the authenticated user and agent."""
    return dispatch("get_operation", {"operation_id": operation_id}, context_id)


@mcp.tool()
def search_tokens(chain: str, query: str, context_id: str = "") -> dict:
    """Search the chain data service token metadata on a specified network. Read only."""
    return dispatch("search_tokens", {"chain": chain, "query": query}, context_id)


@mcp.tool()
def get_token_info(chain: str, token_address: str, context_id: str = "") -> dict:
    """Read token identity and decimals from the chain data service."""
    return dispatch("get_token_info", {"chain": chain, "token_address": token_address}, context_id)


@mcp.tool()
def get_token_price(chain: str, token_address: str, context_id: str = "") -> dict:
    """Read a live token price; missing data is never replaced with an invented price."""
    return dispatch("get_token_price", {"chain": chain, "token_address": token_address}, context_id)


@mcp.tool()
def get_candles(chain: str, token_address: str, bar: str, limit: int, context_id: str = "") -> dict:
    """Read a bounded set of the chain data service candlesticks."""
    return dispatch("get_candles", {"chain": chain, "token_address": token_address, "bar": bar, "limit": limit}, context_id)


@mcp.tool()
def get_token_holders(chain: str, token_address: str, limit: int, context_id: str = "") -> dict:
    """Read public token holder distribution, not the platform's private user accounts."""
    return dispatch("get_token_holders", {"chain": chain, "token_address": token_address, "limit": limit}, context_id)


@mcp.tool()
def scan_token_risk(chain: str, token_address: str, context_id: str = "") -> dict:
    """Read a provider token risk report. Missing or partial data is not proof of safety."""
    return dispatch("scan_token_risk", {"chain": chain, "token_address": token_address}, context_id)


@mcp.tool()
def get_smart_money_signals(chain: str, limit: int, context_id: str = "") -> dict:
    """Read smart money signals for research; grants no copy-trading authority."""
    return dispatch("get_smart_money_signals", {"chain": chain, "limit": limit}, context_id)


@mcp.tool()
def get_swap_quote(chain: str, from_token: str, to_token: str, amount_raw: str, context_id: str = "") -> dict:
    """Read an aggregator quote with amount in token base units. Does not construct or sign a transaction."""
    return dispatch("get_swap_quote", {"chain": chain, "from_token": from_token, "to_token": to_token,
                                       "amount_raw": amount_raw}, context_id)


@mcp.tool()
def get_gas_price(chain: str, context_id: str = "") -> dict:
    """Read current network gas prices for a network."""
    return dispatch("get_gas_price", {"chain": chain}, context_id)


@mcp.tool()
def get_supported_chains(context_id: str = "") -> dict:
    """Read networks supported by the chain data service transaction gateway."""
    return dispatch("get_supported_chains", {}, context_id)


@mcp.tool()
def analyze_portfolio(context_id: str = "") -> dict:
    """Analyze only the authenticated user's simulated holdings, trades and budgets."""
    return dispatch("analyze_portfolio", {}, context_id)


@mcp.tool()
def preview_trade(amount: str, context_id: str = "") -> dict:
    """Check own balance and Agent limits for a USDC amount; never creates approval or executes."""
    return dispatch("preview_trade", {"amount": amount}, context_id)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
