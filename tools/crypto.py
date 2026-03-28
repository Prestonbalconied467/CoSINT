"""
tools/crypto.py  –  Cryptocurrency Wallets & Blockchain
Tools: wallet_btc, wallet_eth, wallet_multi, nft_lookup
"""

import datetime
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from shared import config
from shared.http_client import get, OsintRequestError
from shared.rate_limiter import rate_limit


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations={"readOnlyHint": True})
    async def osint_crypto_wallet_btc(
        address: Annotated[
            str, Field(description="Bitcoin wallet address (base58 or hash160)")
        ],
        limit: Annotated[
            int,
            Field(description="Number of transactions to fetch (1–50)", ge=1, le=50),
        ] = 10,
        offset: Annotated[
            int, Field(description="Skip first n transactions (for pagination)", ge=0)
        ] = 0,
    ) -> str:
        """Bitcoin wallet: balance, transaction count, and paginated TX history via Blockchain.com.

        Returns: balance (BTC + USD), total_sent, total_received, tx_count, first_seen,
          last_seen, and per-transaction data (hash, inputs, outputs, value, timestamp).
        Read transaction patterns as narratives:
          - Large in + immediate full out → pass-through wallet; pivot to destination address
          - Many small incoming → payment collection (merchant, ransom, donations)
          - Single large incoming, untouched → one-time payment or forgotten address
          - Regular periodic → automated (salary, DCA, subscription)
        Do NOT pivot on api.blockchair.com, blockchain.com, or other tool infrastructure domains
          that appear in response metadata — these are tool backends, not target-owned assets.
        Uses Blockchain.com API (free, no key required). Supports pagination via limit/offset.
        """

        address = address.strip()
        try:
            await rate_limit("default")
            data = await get(
                f"https://blockchain.info/rawaddr/{address}",
                params={"limit": limit, "offset": offset},
            )
        except OsintRequestError as e:
            return f"Blockchain.com error: {e.message}"

        balance_btc = data.get("final_balance", 0) / 1e8
        total_recv = data.get("total_received", 0) / 1e8
        total_sent = data.get("total_sent", 0) / 1e8
        n_tx = data.get("n_tx", 0)
        txs = data.get("txs", [])

        lines = [
            f"Bitcoin wallet: {address}\n",
            f"Balance:         {balance_btc:.8f} BTC",
            f"Total received:  {total_recv:.8f} BTC",
            f"Total sent:      {total_sent:.8f} BTC",
            f"Transactions:    {n_tx} total",
        ]

        if txs:
            page_info = f"showing {offset + 1}–{offset + len(txs)}"
            lines.append(f"\n── Transactions ({page_info} of {n_tx}) ──")
            for tx in txs:
                ts = datetime.datetime.fromtimestamp(tx.get("time", 0)).strftime(
                    "%Y-%m-%d %H:%M"
                )
                result_val = tx.get("result", 0) / 1e8
                sign = "+" if result_val >= 0 else ""
                lines.append(
                    f"{ts}  {sign}{result_val:.8f} BTC  [{tx.get('hash', 'N/A')}]"
                )

        return "\n".join(lines)

    ETHERSCAN_CHAINS: dict[str, int] = {
        "ethereum": 1,
        "polygon": 137,
        "arbitrum": 42161,
        "base": 8453,
        "optimism": 10,
        "bnb": 56,
    }

    if config.ETHERSCAN_KEY:

        @mcp.tool(annotations={"readOnlyHint": True})
        async def osint_crypto_wallet_eth(
            address: Annotated[
                str, Field(description="Ethereum-compatible wallet address (0x...)")
            ],
            chain: Annotated[
                str,
                Field(
                    description=f"Chain to query. Options: {', '.join(ETHERSCAN_CHAINS)}"
                ),
            ] = "ethereum",
        ) -> str:
            """Ethereum-compatible wallet: balance, token holdings and transaction history via Etherscan v2.

            Returns: ETH balance, ERC-20 token holdings, recent transactions with counterparty addresses,
              contract interactions, and first/last activity timestamps.
            Supports: Ethereum, Polygon, Arbitrum, Base, Optimism, BNB Chain.
            Key pivot fields: counterparty addresses (especially labeled exchange deposits → KYC exists),
              contract_addresses interacted with (mixer/bridge/DEX usage), first_tx_timestamp (timeline anchor).
            Do NOT pivot on etherscan.io or api.etherscan.io — tool infrastructure, not target assets.
            Requires: ETHERSCAN_KEY in .env (free tier: 5 req/sec)
            """

            address = address.strip()
            chain_id = ETHERSCAN_CHAINS.get(chain.lower(), 1)
            base_params = {
                "chainid": chain_id,
                "address": address,
                "apikey": config.ETHERSCAN_KEY,
            }
            lines: list[str] = [f"Wallet: {address}  [{chain} / chain {chain_id}]\n"]

            # ── Balance ────────────────────────────────────────────────────────────
            try:
                await rate_limit("etherscan")
                data = await get(
                    "https://api.etherscan.io/v2/api",
                    params={
                        **base_params,
                        "module": "account",
                        "action": "balance",
                        "tag": "latest",
                    },
                    max_retries=1,
                )
                balance_eth = int(data.get("result", 0)) / 1e18
                lines.append(f"Balance:  {balance_eth:.6f} ETH")
            except OsintRequestError as e:
                lines.append(f"Balance error: {e.message}")

            # ── Token interactions ─────────────────────────────────────────────────
            try:
                await rate_limit("etherscan")
                data = await get(
                    "https://api.etherscan.io/v2/api",
                    params={
                        **base_params,
                        "module": "account",
                        "action": "tokentx",
                        "sort": "desc",
                    },
                )
                txs = data.get("result", [])
                if isinstance(txs, list) and txs:
                    tokens_seen: dict[str, str] = {}
                    for tx in txs:
                        tokens_seen[tx.get("tokenSymbol", "?")] = tx.get(
                            "tokenName", "?"
                        )
                    lines.append(f"\nInteracted tokens ({len(tokens_seen)}):")
                    for sym, name in list(tokens_seen.items())[:25]:
                        lines.append(f"  {sym:10} {name}")
            except OsintRequestError:
                pass

            # ── Recent TXs ────────────────────────────────────────────────────────
            try:
                await rate_limit("etherscan")
                data = await get(
                    "https://api.etherscan.io/v2/api",
                    params={
                        **base_params,
                        "module": "account",
                        "action": "txlist",
                        "sort": "desc",
                        "page": 1,
                        "offset": 20,
                    },
                )
                txs = data.get("result", [])
                if isinstance(txs, list) and txs:
                    lines.append("\n── Recent Transactions ──")
                    for tx in txs:
                        ts = datetime.datetime.fromtimestamp(
                            int(tx.get("timeStamp", 0))
                        ).strftime("%Y-%m-%d")
                        val = int(tx.get("value", 0)) / 1e18
                        frm = tx.get("from", "?")
                        to = tx.get("to", "?")
                        ok = "✓" if tx.get("isError", "0") == "0" else "✗"
                        lines.append(
                            f"  {ts}  {frm}... → {to}...  {val:.4f} ETH  [{ok}]"
                        )
            except OsintRequestError:
                pass

            return "\n".join(lines)

    @mcp.tool(annotations={"readOnlyHint": True})
    async def osint_crypto_wallet_multi(
        address: Annotated[str, Field(description="Wallet address")],
        chain: Annotated[
            str,
            Field(
                description="Blockchain: bitcoin, ethereum, litecoin, dogecoin, etc."
            ),
        ] = "bitcoin",
    ) -> str:
        """Multi-chain wallet analysis via Blockchair API.

        Returns: balance and transaction summary across supported chains.
        Supports: Bitcoin, Ethereum, Litecoin, Dogecoin, Dash, Bitcoin Cash, and more.
        Use after chain-specific tools to confirm findings and check for cross-chain activity.
        Same EVM address active on multiple chains = likely same operator.
        Do NOT pivot on api.blockchair.com — tool infrastructure, not a target asset.
        Optional: BLOCKCHAIR_KEY for higher rate limits.
        """
        address = address.strip()
        chain = chain.strip().lower()
        params: dict = {}
        if config.BLOCKCHAIR_KEY:
            params["key"] = config.BLOCKCHAIR_KEY

        try:
            await rate_limit("blockchair")
            data = await get(
                f"https://api.blockchair.com/{chain}/dashboards/address/{address}",
                params=params,
            )
        except OsintRequestError as e:
            return f"Blockchair error: {e.message}"

        addr_data = data.get("data", {}).get(address, {})
        addr_info = addr_data.get("address", {})

        if not addr_info:
            return f"No data found for {address} on {chain}."

        balance = addr_info.get("balance", 0)
        received = addr_info.get("received", 0)
        spent = addr_info.get("spent", 0)
        tx_count = addr_info.get("transaction_count", 0)
        divisor = (
            1e8
            if chain in ("bitcoin", "litecoin", "dogecoin", "bitcoin-cash")
            else 1e18
        )

        return (
            f"Blockchair – {chain.title()} wallet:\n"
            f"Address:      {address}\n"
            f"Balance:      {balance / divisor:.8f}\n"
            f"Received:     {received / divisor:.8f}\n"
            f"Spent:        {spent / divisor:.8f}\n"
            f"Transactions: {tx_count}\n"
            f"First TX:     {addr_info.get('first_seen_receiving', 'N/A')}\n"
            f"Last TX:      {addr_info.get('last_seen_receiving', 'N/A')}\n"
            f"Blockchair:   https://blockchair.com/{chain}/address/{address}"
        )

    if config.ALCHEMY_KEY:

        @mcp.tool(annotations={"readOnlyHint": True})
        async def osint_crypto_nft_lookup(
            address: Annotated[
                str, Field(description="Ethereum wallet address (0x...)")
            ],
        ) -> str:
            """Look up NFT holdings of a wallet address via Alchemy NFT API.

            Returns: NFT list, collection names, token IDs, and metadata.
            Key pivot: high-value collection holders (BAYC, CryptoPunks, Azuki) typically have
              a public NFT Twitter/X presence linking wallet to identity — search the wallet address
              and any ENS name on Twitter/X and in the collection's Discord.
            Do NOT use for: transaction history or balance — use osint_crypto_wallet_eth instead.
            Requires: ALCHEMY_KEY in .env
            """

            address = address.strip()
            try:
                await rate_limit("default")
                data = await get(
                    f"https://eth-mainnet.g.alchemy.com/nft/v3/{config.ALCHEMY_KEY}/getNFTsForOwner",
                    params={"owner": address, "pageSize": 20, "withMetadata": "true"},
                )
            except OsintRequestError as e:
                return f"Alchemy error: {e.message}"

            nfts = data.get("ownedNfts", [])
            total = data.get("totalCount", 0)

            if not nfts:
                return f"No NFTs found for {address}."

            lines = [f"NFTs for {address} ({total} total, {len(nfts)} shown):\n"]
            collections: dict[str, int] = {}
            for nft in nfts:
                coll = nft.get("contract", {}).get("name", "Unknown")
                collections[coll] = collections.get(coll, 0) + 1

            lines.append("── Collections ──")
            for coll, count in sorted(collections.items(), key=lambda x: -x[1]):
                lines.append(f"  {coll} {count}×")

            lines.append("\n── NFTs (first 10) ──")
            for nft in nfts[:10]:
                meta = nft.get("name") or f"Token #{nft.get('tokenId', '?')}"
                coll = nft.get("contract", {}).get("name", "N/A")
                token = nft.get("tokenId", "N/A")
                lines.append(f"  [{coll}] {meta}  (Token: {token})")

            return "\n".join(lines)
