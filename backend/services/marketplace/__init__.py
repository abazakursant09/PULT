"""
Marketplace Execution Layer (ME-1).

Single audited, reversible path through which every seller action reaches the
real marketplace. L3 (one-click execute) and L4 (automation) share ONE code
path: `marketplace_executor.execute()`. No router or task may call a marketplace
client directly — only the executor does.

See docs/rfc/ME_marketplace_execution_layer.md.
"""
