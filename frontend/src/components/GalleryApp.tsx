import { useEffect, useMemo, useRef, useState } from 'react';
import type { FormEvent, MouseEvent } from 'react';
import type { MemeItem } from '../lib/types';
import Mascot from './Mascot';
import SubmissionDialog from './SubmissionDialog';

type FormatFilter = 'all' | 'gif' | 'jpeg';
type SortOrder = 'id-desc' | 'name';

type GalleryAppProps = {
  initialTag?: string;
  basePath?: string;
  allowedTags?: string[];
  hideHero?: boolean;
  categoryTitle?: string;
  categoryDesc?: string;
  showAllTags?: boolean;
  onlyAllowedTags?: boolean;
};

const PAGE_SIZE = 48;
const TOP_TAGS = ['开心', '奶蛙', '无语', '猫耳', '加冕', '委屈', '看戏', '睡觉', '摆烂', '大笑'];

function SearchIcon() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5" /><path d="M15.5 15.5 20 20" /></svg>;
}
function DiceIcon() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="4" /><circle cx="8.5" cy="8.5" r="1.3" fill="currentColor" stroke="none" /><circle cx="15.5" cy="15.5" r="1.3" fill="currentColor" stroke="none" /><circle cx="12" cy="12" r="1.3" fill="currentColor" stroke="none" /></svg>;
}
function DownloadIcon() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" aria-hidden="true"><path d="M12 4v11M7 10l5 5 5-5M5 20h14" /></svg>;
}

function normalize(value: string) {
  return value.trim().toLocaleLowerCase();
}

function shuffle<T>(values: T[]): T[] {
  const copy = [...values];
  for (let i = copy.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [copy[i], copy[j]] = [copy[j], copy[i]];
  }
  return copy;
}

function HeroReel({ items }: { items: MemeItem[] }) {
  const [direction, setDirection] = useState<'left' | 'right'>('left');
  const [fullCards, setFullCards] = useState<Set<string>>(new Set());
  const [centerKey, setCenterKey] = useState<string>('');
  const lastCenter = useRef<string>('');
  const sample = useMemo(() => shuffle(items).slice(0, 8), [items]);
  const cards = sample.length ? [...sample, ...sample] : [];
  const windowRef = useRef<HTMLDivElement>(null);

  function openReelCard(event: MouseEvent<HTMLDivElement>) {
    const direct = (event.target as HTMLElement).closest<HTMLAnchorElement>('a.reel-card');
    const under = direct || (document.elementFromPoint(event.clientX, event.clientY)?.closest<HTMLAnchorElement>('a.reel-card') ?? null);
    if (!under) return;
    event.preventDefault();
    window.location.assign(under.href);
  }

  useEffect(() => {
    const root = windowRef.current;
    if (!root || !cards.length) return;
    const cardEls = Array.from(root.querySelectorAll<HTMLElement>('.reel-card'));
    const observer = new IntersectionObserver((entries) => {
      setFullCards((previous) => {
        const next = new Set(previous);
        for (const entry of entries) {
          const key = entry.target.getAttribute('data-reel-key');
          if (!key) continue;
          if (entry.isIntersecting && entry.intersectionRatio >= 0.96) next.add(key);
          else next.delete(key);
        }
        return next;
      });
    }, { root, threshold: [0.96] });
    cardEls.forEach((card) => observer.observe(card));

    // Real cylindrical perspective: cards curve/shrink as they move away from center.
    let raf = 0;
    const tilts = [-2.4, 1.6, -1];
    const lifts = [4, -4, 2];
    const render = () => {
      const rootRect = root.getBoundingClientRect();
      const centerX = rootRect.left + rootRect.width / 2;
      const half = Math.max(1, rootRect.width / 2);
      let nearestIndex = 0;
      let nearestDistance = Number.POSITIVE_INFINITY;
      const positions = cardEls.map((card) => {
        const rect = card.getBoundingClientRect();
        const cardCenter = rect.left + rect.width / 2;
        const distance = Math.abs(cardCenter - centerX);
        if (distance < nearestDistance) {
          nearestDistance = distance;
          nearestIndex = cardEls.indexOf(card);
        }
        return {
          normalized: Math.max(-1, Math.min(1, (cardCenter - centerX) / half)),
          distance,
        };
      });
      const nearestKey = cardEls[nearestIndex]?.getAttribute('data-reel-key') || '';
      if (nearestKey && nearestKey !== lastCenter.current) {
        lastCenter.current = nearestKey;
        setCenterKey(nearestKey);
      }
      cardEls.forEach((card, index) => {
        const { normalized } = positions[index];
        const abs = Math.abs(normalized);
        const rotateY = normalized * -20;
        const scale = 1 - abs * 0.22;
        card.style.transform = `rotate(${tilts[index % 3]}deg) translateY(${lifts[index % 3]}px) rotateY(${rotateY}deg) scale(${scale})`;
        card.style.opacity = String(1 - abs * 0.28);
        card.style.boxShadow = `${(-normalized * 8).toFixed(1)}px 7px 0 rgba(34,49,59,.12)`;
      });
      raf = requestAnimationFrame(render);
    };
    raf = requestAnimationFrame(render);
    return () => { observer.disconnect(); cancelAnimationFrame(raf); };
  }, [cards.length]);

  return (
    <div className="hero-reel" aria-label="随机表情滚动展示">
      <div className="reel-window" ref={windowRef} onClick={openReelCard}>
        {cards.length === 0 && (
          <div className="reel-skeleton">
            {Array.from({ length: 5 }).map((_, index) => <span key={index} />)}
          </div>
        )}
        {cards.length > 0 && (
          <div className={`reel-track ${direction}`}>
            {cards.map((item, index) => {
              const key = `${item.id}-${index}`;
              return (
                <a
                  className={`reel-card ${index % 3 === 0 ? 'tilt-a' : index % 3 === 1 ? 'tilt-b' : 'tilt-c'} ${fullCards.has(key) ? 'is-full' : ''} ${centerKey === key ? 'is-center' : ''}`}
                  data-reel-key={key}
                  key={key}
                  href={`/meme/${item.id}`}
                  aria-hidden={index >= sample.length}
                >
                  <div className="reel-photo"><img src={item.thumb640} alt={item.name} loading="eager" /></div>
                  <div className="reel-name">{item.name}</div>
                </a>
              );
            })}
          </div>
        )}
      </div>
      <div className="reel-bar">
        <span><i className="live-dot" /> 自动放映 · 悬停暂停</span>
        <div className="reel-controls">
          <button type="button" onClick={() => setDirection('right')} className={direction === 'right' ? 'active' : ''} aria-label="向右滚动">‹</button>
          <button type="button" onClick={() => setDirection('left')} className={direction === 'left' ? 'active' : ''} aria-label="向左滚动">›</button>
        </div>
      </div>
    </div>
  );
}

export default function GalleryApp({
  initialTag = '',
  basePath = '/',
  allowedTags,
  hideHero = false,
  categoryTitle = '',
  categoryDesc = '',
  showAllTags = false,
  onlyAllowedTags = false,
}: GalleryAppProps) {
  const [items, setItems] = useState<MemeItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [queryInput, setQueryInput] = useState('');
  const [tag, setTag] = useState(initialTag);
  const [format, setFormat] = useState<FormatFilter>('all');
  const [sort, setSort] = useState<SortOrder>('id-desc');
  const [visible, setVisible] = useState(PAGE_SIZE);
  const [selected, setSelected] = useState<MemeItem | null>(null);
  const [copied, setCopied] = useState(false);
  const [searchIds, setSearchIds] = useState<Set<string> | null>(null);
  const [searchOrder, setSearchOrder] = useState<Map<string, number>>(new Map());
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchFallback, setSearchFallback] = useState(false);
  const [submissionOpen, setSubmissionOpen] = useState(false);
  const [tagsExpanded, setTagsExpanded] = useState(false);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const openedRandom = useRef(false);

  async function loadGallery() {
    setLoading(true);
    setError(null);
    try {
      const all: MemeItem[] = [];
      let offset = 0;
      let total = 0;
      do {
        const response = await fetch(`/api/gallery?limit=100&offset=${offset}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload = await response.json();
        total = payload.total;
        for (const item of payload.items) {
          all.push({
            id: item.id,
            name: item.name,
            tag: item.tag,
            format: item.format,
            width: item.width,
            height: item.height,
            bytes: item.bytes,
            image: `/api/images/${item.id}`,
            download: `/api/images/${item.id}?download=1`,
            thumb320: `/api/thumbs/${item.id}/320`,
            thumb640: `/api/thumbs/${item.id}/640`,
          });
        }
        offset += payload.items.length;
        if (!payload.items.length) break;
      } while (offset < total);
      setItems(all);
      setLoading(false);
    } catch (err) {
      console.error(err);
      setError('图库加载失败，请确认本地 API 正在运行。');
      setLoading(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    (async () => {
      await loadGallery();
      if (cancelled) return;
      const params = new URLSearchParams(window.location.search);
      const q = params.get('q') || '';
      const tagParam = params.get('tag') || initialTag;
      setQuery(q);
      setQueryInput(q);
      setTag(tagParam);
      if (params.get('random') === '1') openedRandom.current = true;
    })();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!openedRandom.current || !items.length) return;
    openedRandom.current = false;
    const pick = items[Math.floor(Math.random() * items.length)];
    setSelected(pick);
  }, [items]);

  useEffect(() => {
    if (!query) {
      setSearchIds(null);
      setSearchOrder(new Map());
      setSearchFallback(false);
      setSearchLoading(false);
      return;
    }
    let cancelled = false;
    const controller = new AbortController();
    setSearchLoading(true);
    const timer = window.setTimeout(async () => {
      try {
        const response = await fetch(`/api/search/list?q=${encodeURIComponent(query)}&limit=100`, { signal: controller.signal });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload = await response.json();
        if (cancelled) return;
        setSearchIds(new Set(payload.items.map((item: { id: string }) => item.id)));
        setSearchOrder(new Map(payload.items.map((item: { id: string }, index: number) => [item.id, index])));
        setSearchFallback(Boolean(payload.fallback));
      } catch (err) {
        if (cancelled || (err instanceof DOMException && err.name === 'AbortError')) return;
        console.error(err);
        setSearchIds(null);
        setSearchOrder(new Map());
      } finally {
        if (!cancelled) setSearchLoading(false);
      }
    }, 260);
    return () => { cancelled = true; window.clearTimeout(timer); controller.abort(); };
  }, [query]);

  const tagCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const item of items) for (const value of item.tag) counts.set(value, (counts.get(value) || 0) + 1);
    return counts;
  }, [items]);

  const knownTags = useMemo(() => [...tagCounts.keys()].sort((a, b) => b.length - a.length), [tagCounts]);
  const compoundTags = useMemo(() => {
    if (!query.trim()) return [] as string[];
    let rest = query.trim();
    const found: string[] = [];
    for (const value of knownTags) {
      if (rest.includes(value)) {
        found.push(value);
        rest = rest.replace(value, ' ');
      }
    }
    return found.length >= 2 ? found : [];
  }, [query, knownTags]);

  const tagChoices = useMemo(() => {
    const source = allowedTags ? allowedTags.filter((value) => tagCounts.has(value)) : knownTags;
    return [...new Set(source)];
  }, [allowedTags, knownTags, tagCounts]);

  const visibleTagChoices = showAllTags || tagsExpanded ? tagChoices : tagChoices.slice(0, 18);

  const heroTags = useMemo(() => {
    const source = tagChoices.length ? tagChoices : knownTags;
    const preferred = TOP_TAGS.filter((value) => source.includes(value));
    return [...new Set([...preferred, ...source.filter((value) => !preferred.includes(value))])].slice(0, 8);
  }, [knownTags, tagChoices]);

  const filtered = useMemo(() => {
    const needle = normalize(query);
    const result = items.filter((item) => {
      if (tag && !item.tag.includes(tag)) return false;
      if (!tag && !query && onlyAllowedTags && allowedTags?.length && !item.tag.some((value) => allowedTags.includes(value))) return false;
      if (format !== 'all' && item.format !== format) return false;
      if (!needle) return true;
      if (compoundTags.length >= 2) return compoundTags.every((value) => item.tag.includes(value));
      if (searchIds) return searchIds.has(item.id);
      return normalize(item.name).includes(needle) || item.tag.some((value) => normalize(value).includes(needle));
    });
    result.sort((a, b) => {
      if (query && searchOrder.size) {
        const ai = searchOrder.get(a.id) ?? Number.MAX_SAFE_INTEGER;
        const bi = searchOrder.get(b.id) ?? Number.MAX_SAFE_INTEGER;
        return ai - bi;
      }
      if (sort === 'name') return a.name.localeCompare(b.name, 'zh-CN');
      return Number(b.id) - Number(a.id);
    });
    return result;
  }, [items, query, tag, format, sort, searchIds, searchOrder, compoundTags, onlyAllowedTags, allowedTags]);

  const visibleItems = filtered.slice(0, visible);

  useEffect(() => { setVisible(PAGE_SIZE); }, [query, tag, format, sort]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (selected && !dialog.open) dialog.showModal();
    if (!selected && dialog.open) dialog.close();
  }, [selected]);

  function updateUrl(params: URLSearchParams) {
    const qs = params.toString();
    window.history.replaceState({}, '', `${basePath}${qs ? `?${qs}` : ''}`);
  }

  function submitSearch(event?: FormEvent) {
    event?.preventDefault();
    const next = queryInput.trim();
    setQuery(next);
    setVisible(PAGE_SIZE);
    const params = new URLSearchParams(window.location.search);
    if (next) params.set('q', next); else params.delete('q');
    updateUrl(params);
  }

  function selectTag(nextTag: string) {
    setTag(nextTag);
    setQuery('');
    setQueryInput('');
    const params = new URLSearchParams(window.location.search);
    params.delete('q');
    if (nextTag) params.set('tag', nextTag); else params.delete('tag');
    updateUrl(params);
  }

  function clearFilters() {
    setTag('');
    setQuery('');
    setQueryInput('');
    setFormat('all');
    setSort('id-desc');
    updateUrl(new URLSearchParams());
  }

  function randomItem() {
    const pool = filtered.length ? filtered : items;
    if (!pool.length) return;
    setSelected(pool[Math.floor(Math.random() * pool.length)]);
  }

  function step(direction: -1 | 1) {
    if (!selected) return;
    const index = filtered.findIndex((item) => item.id === selected.id);
    if (index < 0) { randomItem(); return; }
    const next = filtered[(index + direction + filtered.length) % filtered.length];
    if (next) setSelected(next);
  }

  async function copyLink() {
    if (!selected) return;
    const url = `${window.location.origin}/meme/${selected.id}`;
    try {
      await navigator.clipboard.writeText(url);
    } catch {
      window.prompt('复制链接', url);
    }
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1400);
  }

  return (
    <>
      <main>
        {!hideHero && (
          <section className="hero shell">
            <div className="hero-grid">
              <div className="hero-copy">
                <div className="eyebrow"><span className="dot" /> {items.length || 1164} 张奶龙 · 奶蛙表情库</div>
                <h1>今天翻哪只<br /><span className="mark">奶龙</span>？</h1>
                <p className="hero-desc">收录 <strong>{items.length || 1164}</strong> 张奶龙与奶蛙表情，按心情、角色、状态随手翻。找到今天想发的那一张，点开就能下载。</p>
                <form className="hero-search" onSubmit={submitSearch}>
                  <SearchIcon />
                  <input type="search" value={queryInput} onChange={(event) => setQueryInput(event.target.value)} placeholder="搜「开心」「奶蛙大笑」或 id1181…" aria-label="搜索表情" />
                  <button className="btn btn-primary" type="submit"><span>搜一下</span></button>
                </form>
                <div className="search-hint"><span>同音字也能搜：</span><kbd>开新</kbd><span>→ 开心</span><span>·</span><span>组合标签：奶蛙大笑</span><span>·</span><span>ID 直达：</span><kbd>id1181</kbd></div>
                <div className="tag-row" aria-label="热门标签">
                  {heroTags.map((value) => (
                    <button className={`tag ${tag === value ? 'hot' : ''}`} key={value} type="button" onClick={() => selectTag(value)}><span className="hash">#</span>{value}</button>
                  ))}
                  <a className="tag" href="/tags"><span className="hash">#</span>更多</a>
                </div>
                <div className="hero-submit-row">
                  <button className="hero-submit" type="button" onClick={() => setSubmissionOpen(true)}>
                    <span className="hero-submit-mark">＋</span>
                    <span><b>我也要投稿</b><small>分享一张好用的奶龙表情</small></span>
                    <span className="hero-submit-arrow">↗</span>
                  </button>
                  <span className="hero-submit-note">审核通过后公开</span>
                </div>
                <div className="hero-meta">
                  <div><b>{items.length || 1164}</b><span>公开图片</span></div>
                  <div><b>{tagCounts.size || 303}</b><span>标签</span></div>
                  <div><b>免费</b><span>单张下载</span></div>
                </div>
              </div>
              <HeroReel items={items} />
            </div>
          </section>
        )}

        {hideHero && (
          <section className="category-hero shell">
            <div className="eyebrow"><span className="dot" /> {items.length || 1164} 张公开表情</div>
            <h1>{categoryTitle || '随便翻翻'}</h1>
            {categoryDesc && <p>{categoryDesc}</p>}
            {tagChoices.length > 0 && (
              <div className={`category-tags ${showAllTags ? 'all-tags' : ''}`} aria-label="分类标签">
                {visibleTagChoices.map((value) => (
                  <button className={`tag ${tag === value ? 'hot' : ''}`} key={value} type="button" onClick={() => selectTag(value)}>
                    <span className="hash">#</span>{value}<small>{tagCounts.get(value)}</small>
                  </button>
                ))}
                {!showAllTags && tagChoices.length > visibleTagChoices.length && (
                  <button className="tag tag-more" type="button" onClick={() => setTagsExpanded(true)}>更多标签 +{tagChoices.length - visibleTagChoices.length}</button>
                )}
              </div>
            )}
          </section>
        )}

        <section className="library shell" id="library">
          <div className="library-head">
            <div>
              <div className="section-kicker">THE MEME SHELF</div>
              <h2 className="section-title">{tag ? `#${tag}` : '随便翻翻'}</h2>
              <p className="section-desc">{query ? `正在看「${query}」相关表情` : '点开任意一张看大图，喜欢就带走。'}</p>
            </div>
            <button className="btn btn-yellow library-random" type="button" onClick={randomItem}><DiceIcon /> 随机一张</button>
          </div>

          <div className="toolbar" aria-label="筛选">
            <div className="seg" role="group" aria-label="全部、动图、静图">
              <button className={format === 'all' ? 'active' : ''} type="button" onClick={() => setFormat('all')}>全部</button>
              <button className={format === 'gif' ? 'active' : ''} type="button" onClick={() => setFormat('gif')}>动图</button>
              <button className={format === 'jpeg' ? 'active' : ''} type="button" onClick={() => setFormat('jpeg')}>静图</button>
            </div>
            <div className="divider" />
            <div className="seg" role="group" aria-label="排序">
              <button className={sort === 'id-desc' ? 'active' : ''} type="button" onClick={() => setSort('id-desc')}>默认</button>
              <button className={sort === 'name' ? 'active' : ''} type="button" onClick={() => setSort('name')}>名称</button>
            </div>
            <div className="divider" />
            <button className="btn btn-sm btn-ghost" type="button" onClick={() => selectTag('奶蛙')}>只看奶蛙</button>
            <button className="btn btn-sm btn-ghost" type="button" onClick={clearFilters}>清除筛选</button>
            <span className="result-count">
              {searchLoading && !compoundTags.length ? '正在智能匹配…' : <>已显示 <b>{visibleItems.length}</b> / {filtered.length} 张</>}
              {searchFallback && !searchLoading && !compoundTags.length ? ' · 无精确匹配' : ''}
            </span>
          </div>

          {loading && <div className="gallery-loading"><span className="loading-dots">正在取表情</span></div>}
          {error && <div className="gallery-error">{error}<br /><button className="btn btn-sm" type="button" onClick={loadGallery}>重试</button></div>}
          {!loading && !error && !visibleItems.length && (
            <div className="gallery-empty"><b>没有找到这类表情</b><br />换个词，或者让随机按钮帮你挑一张。<br /><button className="btn btn-yellow" type="button" onClick={randomItem}>随机一张</button></div>
          )}

          {!loading && !error && !!visibleItems.length && (
            <>
              <div className="grid">
                {visibleItems.map((item, index) => (
                  <article className="meme-card" key={item.id} onClick={() => setSelected(item)}>
                    <div className="thumb">
                      <span className={`badge ${item.format === 'gif' ? 'gif' : ''}`}>{item.format === 'gif' ? 'GIF' : 'JPG'}</span>
                      <img
                        src={item.thumb320}
                        srcSet={`${item.thumb320} 320w, ${item.thumb640} 640w`}
                        sizes="(max-width: 560px) 45vw, (max-width: 1100px) 22vw, 15vw"
                        alt={item.name}
                        loading={index < 12 ? 'eager' : 'lazy'}
                        decoding="async"
                        width={item.width || undefined}
                        height={item.height || undefined}
                      />
                      <a className="quick-dl" href={item.download} aria-label={`下载 ${item.name}`} onClick={(event) => event.stopPropagation()}><DownloadIcon /></a>
                    </div>
                    <div className="card-body">
                      <div className="card-title"><a href={`/meme/${item.id}`} onClick={(event) => event.stopPropagation()}>{item.name}</a></div>
                      <div className="card-tags">
                        {[...item.tag].sort((a, b) => (allowedTags?.includes(b) ? 1 : 0) - (allowedTags?.includes(a) ? 1 : 0)).slice(0, 2).map((value) => <span key={value}>{value}</span>)}
                      </div>
                    </div>
                  </article>
                ))}
              </div>
              {visible < filtered.length && (
                <div className="load-more"><button className="btn btn-yellow" type="button" onClick={() => setVisible((value) => value + PAGE_SIZE)}>再看一些 ↓</button></div>
              )}
            </>
          )}
        </section>
      </main>

      <a className="fab-random" href="#random" onClick={(event) => { event.preventDefault(); randomItem(); }} aria-label="随机一张"><DiceIcon /> 随机一张</a>

      {!hideHero && <Mascot items={items} />}
      {!hideHero && <SubmissionDialog open={submissionOpen} onClose={() => setSubmissionOpen(false)} />}

      <dialog className="lightbox" ref={dialogRef} onClose={() => setSelected(null)} onCancel={() => setSelected(null)}>
        {selected && (
          <>
            <div className="lightbox-stage">
              <button className="lightbox-close" type="button" onClick={() => setSelected(null)} aria-label="关闭">×</button>
              <button className="lightbox-arrow prev" type="button" onClick={() => step(-1)} aria-label="上一张">‹</button>
              <img src={selected.image} alt={selected.name} />
              <button className="lightbox-arrow next" type="button" onClick={() => step(1)} aria-label="下一张">›</button>
            </div>
            <div className="lightbox-info">
              <div><h2>{selected.name}</h2><div className="detail-tags">{selected.tag.map((value) => <span className="tag" key={value}><span className="hash">#</span>{value}</span>)}</div></div>
              <div className="lightbox-actions">
                <a className="btn btn-primary" href={selected.download}><DownloadIcon /> 下载原图</a>
                <a className="btn" href={`/meme/${selected.id}`}>详情</a>
                <button className="btn" type="button" onClick={copyLink}>{copied ? '已复制' : '复制链接'}</button>
              </div>
            </div>
          </>
        )}
      </dialog>
    </>
  );
}
