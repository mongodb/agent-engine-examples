"""Simple Agent Tools — Web search with crawl4ai and DuckDuckGo."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable
from typing import Any

# crawl4ai hardcodes its DB path to /root/.crawl4ai at import time.
# The platform enforces ReadOnlyRootFilesystem, so we must redirect it
# to a writable location before the library is imported.
os.environ.setdefault("CRAWL4_AI_BASE_DIRECTORY", "/tmp")

from crawl4ai import (
    AsyncWebCrawler,
    BM25ContentFilter,
    CrawlResult,
    DefaultMarkdownGenerator,
    HTTPCrawlerConfig,
    LXMLWebScrapingStrategy,
    PruningContentFilter,
)
from crawl4ai.async_configs import CacheMode, CrawlerRunConfig
from crawl4ai.async_crawler_strategy import AsyncHTTPCrawlerStrategy
from crawl4ai.async_dispatcher import SemaphoreDispatcher
from duckduckgo_search import DDGS
from duckduckgo_search.exceptions import DuckDuckGoSearchException
from httpx import TimeoutException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

ToolMap = dict[str, Callable[..., str]]

# Using US region because only duckduckgo backend supports "global" (wt-wt) region
SEARCH_REGION = "us-en&setlang=en"
SEARCH_BACKEND = "duckduckgo,bing,google"


class WebSearchResult(BaseModel):
    url: str
    title: str
    markdown: str | None = None


def register(app: Any) -> ToolMap:
    """Register web search tool on the App instance."""

    @app.tool(is_local=False)
    def web_search(query: str, max_results: int = 5) -> str:
        """Search the web for the query and return the results. Defaults to 5 results."""
        logger.info("web_search called: query=%s, max_results=%d", query, max_results)

        try:
            search_results = asyncio.run(_get_relevant_links(query, max_results))
            crawl_results = asyncio.run(_crawl_content(search_results, search_term=query))
        except Exception:
            logger.exception("Search and crawl failed")
            return "Search tool failed while searching for results"

        if not crawl_results:
            return "No results found"

        result_message = ""
        for result in crawl_results:
            result_message += (
                f"<document>TITLE: {result.title}\nURL: {result.url}\n{result.markdown}</document>"
            )
        return result_message

    return {"web_search": web_search}


async def _get_relevant_links(search_term: str, max_results: int) -> list[WebSearchResult]:
    """Search DuckDuckGo and extract a list of results and titles."""
    logger.info("Searching: %s", search_term)
    try:
        search = DDGS()
        search_results = search.text(
            query=search_term,
            region=SEARCH_REGION,
            max_results=max_results,
            backend=SEARCH_BACKEND,
        )
        return [
            WebSearchResult(url=result["href"], title=result["title"]) for result in search_results
        ]
    except (DuckDuckGoSearchException, TimeoutException) as e:
        logger.warning("Error retrieving search results: %s", e)
        msg = f"Error retrieving search results for {search_term}: {e}"
        raise RuntimeError(msg) from e


async def _crawl_content(
    search_results: list[WebSearchResult], search_term: str | None = None
) -> list[WebSearchResult]:
    """Crawl a list of URLs and obtain their text content."""
    start_time = time.perf_counter()
    if not search_results:
        return []

    urls = [r.url for r in search_results]
    crawl_results = await _crawl_urls(urls=urls, user_query=search_term)

    logger.info(
        "Crawl results: %.3fs, %d results",
        time.perf_counter() - start_time,
        len(crawl_results),
    )

    return [
        WebSearchResult(url=r.url, title=r.title, markdown=crawl_results.get(r.url))
        for r in search_results
        if crawl_results.get(r.url)
    ]


async def _crawl_urls(urls: list[str], user_query: str | None = None) -> dict[str, str]:
    """Crawl multiple URLs concurrently using crawl4ai."""
    dispatcher = SemaphoreDispatcher(max_session_permit=min(10, len(urls)))
    logger.info("Crawling %d URLs", len(urls))

    try:
        content_filter = (
            BM25ContentFilter(user_query=user_query) if user_query else PruningContentFilter()
        )
        run_config = CrawlerRunConfig(
            word_count_threshold=10,
            exclude_external_links=True,
            remove_overlay_elements=True,
            process_iframes=False,
            excluded_tags=["nav", "footer", "header"],
            cache_mode=CacheMode.DISABLED,
            scraping_strategy=LXMLWebScrapingStrategy(),
            page_timeout=5000,
            markdown_generator=DefaultMarkdownGenerator(
                content_filter=content_filter,
                options={"ignore_links": True, "ignore_images": True},
            ),
        )
        http_crawler_config = HTTPCrawlerConfig(
            method="GET", follow_redirects=True, verify_ssl=True
        )
        async with AsyncWebCrawler(
            crawler_strategy=AsyncHTTPCrawlerStrategy(browser_config=http_crawler_config),
        ) as crawler:
            run_results = await crawler.arun_many(
                urls=urls,
                config=run_config,
                dispatcher=dispatcher,
            )
            results = [result for container in run_results for result in container]  # type: ignore[union-attr]
            return {
                result.url: result.markdown.fit_markdown
                for result in results
                if isinstance(result, CrawlResult) and result.success and result.markdown
            }
    except Exception:
        logger.exception("arun_many failed")
        return {}
