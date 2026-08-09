import "@testing-library/jest-dom/vitest";

// jsdom 不实现 scrollIntoView，但键盘导航的列表（全局搜索、模型选择器）都要靠它
// 把高亮项滚进视野。组件里不加防御 —— 真实浏览器一定有，缺的是测试环境。
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => undefined;
}

// 列表刷新会把滚动位置拨回顶部，jsdom 的 Element 也没有这个方法。
if (!Element.prototype.scrollTo) {
  Element.prototype.scrollTo = () => undefined;
}

// 同理：ResizeObserver 用来实测 composer 高度，jsdom 里没有。
if (!globalThis.ResizeObserver) {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}
