"""用于手动验证 PaperMind FastAPI 接口的最小 Gradio 页面。"""

import json
import os
from typing import Any

import gradio as gr
import httpx


DEFAULT_API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")


def format_search_results(results: list[dict[str, Any]]) -> str:
    """将检索结果渲染为便于人工核对的 Markdown 卡片。"""
    if not results:
        return "未检索到结果。"

    cards = []
    for index, result in enumerate(results, start=1):
        title = result.get("title", "未命名论文")
        url = result.get("url", "")
        text = result.get("text", "")
        score = result.get("score")
        score_text = "" if score is None else f" · 距离：{score:.4f}"
        link = f"[打开论文]({url})" if url else "无论文链接"
        cards.append(f"### {index}. {title}{score_text}\n\n{text}\n\n{link}")
    return "\n\n---\n\n".join(cards)


def format_sources(sources: list[dict[str, Any]]) -> str:
    """将 RAG 来源渲染为带原文片段的 Markdown 卡片。"""
    if not sources:
        return "暂无可展示的来源。"
    return format_search_results(sources)


def _api_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}{path}"


def _request(
    method: str, base_url: str, path: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    """请求本地 FastAPI，并统一转换网络和接口错误。"""
    try:
        response = httpx.request(
            method,
            _api_url(base_url, path),
            json=payload,
            timeout=120.0,
        )
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError as exc:
        return {"error": f"请求失败：{exc}"}
    except ValueError:
        return {"error": "接口未返回 JSON 响应。"}

    if not isinstance(data, dict):
        return {"error": "接口返回了非对象格式的数据。"}
    return data


def check_health(base_url: str) -> str:
    """检查当前 FastAPI 服务是否可访问。"""
    return _format_json(_request("GET", base_url, "/health"))


def collect_papers(base_url: str, query: str, max_results: int) -> str:
    """调用论文采集接口，并显示完整响应。"""
    return _format_json(
        _request(
            "POST",
            base_url,
            "/papers/collect",
            {"query": query, "max_results": int(max_results)},
        )
    )


def build_index(base_url: str) -> str:
    """调用索引构建接口，并显示论文数与文本块数。"""
    return _format_json(_request("POST", base_url, "/index/build", {}))


def search_papers(base_url: str, query: str, top_k: int) -> str:
    """调用语义检索接口，并展示命中片段。"""
    data = _request("POST", base_url, "/search", {"query": query, "top_k": int(top_k)})
    if "error" in data:
        return data["error"]
    return format_search_results(data.get("results", []))


def ask_question(base_url: str, question: str, top_k: int) -> tuple[str, str]:
    """调用 RAG 问答接口，并分别返回答案与来源。"""
    data = _request("POST", base_url, "/ask", {"question": question, "top_k": int(top_k)})
    if "error" in data:
        return data["error"], "暂无可展示的来源。"
    return data.get("answer", ""), format_sources(data.get("sources", []))


def _format_json(data: dict[str, Any]) -> str:
    return f"```json\n{json.dumps(data, ensure_ascii=False, indent=2)}\n```"


def build_demo() -> gr.Blocks:
    """创建用于采集、索引、检索与问答联调的 Gradio 页面。"""
    with gr.Blocks(title="PaperMind 测试台") as demo:
        gr.Markdown("# PaperMind 测试台\n直接调用当前 FastAPI 服务，便于核对检索片段与回答来源。")
        with gr.Row():
            api_base_url = gr.Textbox(
                label="FastAPI 地址",
                value=DEFAULT_API_BASE_URL,
                scale=4,
            )
            health_button = gr.Button("检查服务", scale=1)
        health_output = gr.Markdown()
        health_button.click(check_health, inputs=api_base_url, outputs=health_output)

        with gr.Tab("采集与索引"):
            with gr.Row():
                collect_query = gr.Textbox(label="研究关键词", placeholder="例如：disinformation")
                collect_count = gr.Slider(1, 30, value=5, step=1, label="采集篇数")
            collect_button = gr.Button("采集论文", variant="primary")
            collect_output = gr.Markdown()
            collect_button.click(
                collect_papers,
                inputs=[api_base_url, collect_query, collect_count],
                outputs=collect_output,
            )
            index_button = gr.Button("构建/更新索引")
            index_output = gr.Markdown()
            index_button.click(build_index, inputs=api_base_url, outputs=index_output)

        with gr.Tab("语义检索"):
            search_query = gr.Textbox(label="检索问题", placeholder="例如：multimodal disinformation detection")
            search_top_k = gr.Slider(1, 10, value=3, step=1, label="Top-K")
            search_button = gr.Button("检索", variant="primary")
            search_output = gr.Markdown(label="命中片段")
            search_button.click(
                search_papers,
                inputs=[api_base_url, search_query, search_top_k],
                outputs=search_output,
            )

        with gr.Tab("RAG 问答"):
            ask_input = gr.Textbox(label="问题", lines=3, placeholder="请输入需要基于论文库回答的问题")
            ask_top_k = gr.Slider(1, 10, value=3, step=1, label="Top-K")
            ask_button = gr.Button("开始问答", variant="primary")
            answer_output = gr.Markdown(label="回答")
            source_output = gr.Markdown(label="来源与片段")
            ask_button.click(
                ask_question,
                inputs=[api_base_url, ask_input, ask_top_k],
                outputs=[answer_output, source_output],
            )
    return demo


if __name__ == "__main__":
    build_demo().launch(
        server_name=os.getenv("GRADIO_SERVER_NAME", "127.0.0.1"),
        server_port=int(os.getenv("GRADIO_SERVER_PORT", "7860")),
    )
