import { useEffect, useMemo, useRef, useState } from 'react';
import type { MemeItem } from '../lib/types';
import pet from '../data/pet.json';

type MascotAction = 'idle' | 'wait' | 'review' | 'sleep' | 'cheer';

type Animation = { id: string; row: number; durations: number[] };

const SHEET = '/mascot/naifrog-sheet.webp';
const COLUMNS = pet.columns;
const ROWS = pet.rows;
const FRAME_W = pet.frameWidth;
const FRAME_H = pet.frameHeight;
const IDLE_ACTIONS: MascotAction[] = ['idle', 'wait', 'review', 'sleep'];
const ACTION_LABEL: Record<MascotAction, string> = {
  idle: '待机',
  wait: '张望',
  review: '研究',
  sleep: '打盹',
  cheer: '哈哈哈哈',
};
const BUBBLE_IDS = ['3', '4', '5', '6', '8', '13', '14', '21', '22', '827', '949'];
const IDLE_AFTER = 4200;
const HIDE_AFTER = 24000;
const CHEER_MS = 2400;

const animations = new Map<string, Animation>(
  (pet.animations as Animation[]).map((item) => [item.id, item]),
);

function animationFor(action: MascotAction): Animation {
  return animations.get(action) || animations.get('idle') || { id: 'idle', row: 0, durations: [500] };
}

export default function Mascot({ items }: { items: MemeItem[] }) {
  const [action, setAction] = useState<MascotAction>('idle');
  const [frame, setFrame] = useState(0);
  const [peek, setPeek] = useState(false);
  const [bubbleId, setBubbleId] = useState<string | null>(null);
  const [touch, setTouch] = useState(0);
  const [cheerNonce, setCheerNonce] = useState(0);
  const bubblePending = useRef(false);
  const bubbleTimer = useRef<number | null>(null);
  const cheerTimer = useRef<number | null>(null);
  const byId = useMemo(() => new Map(items.map((item) => [item.id, item])), [items]);
  const bubblePool = useMemo(() => items.filter((item) => BUBBLE_IDS.includes(item.id)), [items]);
  const bubble = bubbleId ? byId.get(bubbleId) || null : null;
  const current = animationFor(action);

  // Sprite-sheet frame clock (per action row; durations come from pet.json).
  useEffect(() => {
    const durations = current.durations;
    let index = 0;
    let timeout = 0;
    const tick = () => {
      setFrame(index);
      timeout = window.setTimeout(() => {
        index = (index + 1) % durations.length;
        tick();
      }, durations[index] || 150);
    };
    tick();
    return () => window.clearTimeout(timeout);
  }, [current]);

  // After the clap/laugh, return to a calm idle action.
  useEffect(() => {
    if (action !== 'cheer') return;
    if (cheerTimer.current) window.clearTimeout(cheerTimer.current);
    cheerTimer.current = window.setTimeout(() => setAction('idle'), CHEER_MS);
    return () => { if (cheerTimer.current) window.clearTimeout(cheerTimer.current); };
  }, [action, cheerNonce]);

  // Pick an actual sprite idle action every few seconds (not a gallery image).
  useEffect(() => {
    if (peek || action === 'cheer') return;
    const wait = action === 'sleep' ? 6200 : IDLE_AFTER + Math.random() * 2200;
    const timer = window.setTimeout(() => {
      const options = IDLE_ACTIONS.filter((value) => value !== action);
      setAction(options[Math.floor(Math.random() * options.length)] || 'idle');
    }, wait);
    return () => window.clearTimeout(timer);
  }, [action, peek]);

  // Inactivity: hide to the nearest board edge and only show the head.
  useEffect(() => {
    if (peek) return;
    const timer = window.setTimeout(() => {
      setPeek(true);
      setBubbleId(null);
      bubblePending.current = false;
    }, HIDE_AFTER);
    return () => window.clearTimeout(timer);
  }, [peek, touch]);

  // If the visitor clicks before the gallery finishes loading, fill the bubble later.
  useEffect(() => {
    if (!bubblePending.current || bubbleId || !bubblePool.length) return;
    const next = bubblePool[Math.floor(Math.random() * bubblePool.length)];
    if (next) {
      bubblePending.current = false;
      setBubbleId(next.id);
    }
  }, [bubblePool, bubbleId]);

  useEffect(() => () => {
    if (bubbleTimer.current) window.clearTimeout(bubbleTimer.current);
    if (cheerTimer.current) window.clearTimeout(cheerTimer.current);
  }, []);

  function startBubble() {
    if (bubbleTimer.current) window.clearTimeout(bubbleTimer.current);
    if (!bubblePool.length) {
      bubblePending.current = true;
      setBubbleId(null);
      return;
    }
    const next = bubblePool[Math.floor(Math.random() * bubblePool.length)];
    bubblePending.current = false;
    setBubbleId(next?.id || null);
    bubbleTimer.current = window.setTimeout(() => setBubbleId(null), 3800);
  }

  function poke() {
    setTouch((value) => value + 1);
    setPeek(false);
    setAction('cheer');
    setCheerNonce((value) => value + 1);
    startBubble();
  }

  const col = Math.min(COLUMNS - 1, frame);
  const row = Math.min(ROWS - 1, current.row);
  const spriteTransform = `translate(${(-col * 100 / COLUMNS).toFixed(4)}%, ${(-row * 100 / ROWS).toFixed(4)}%)`;

  return (
    <div className={`mascot is-${peek ? 'peek' : action}`} aria-live="polite">
      {bubble && (
        <div className="mascot-bubble">
          <a href={`/meme/${bubble.id}`} onClick={(event) => event.stopPropagation()}>
            <img src={bubble.thumb640} alt={bubble.name} />
            <span>{bubble.name}</span>
          </a>
        </div>
      )}
      <button className="mascot-hit" type="button" onClick={poke} aria-label="戳一下奶蛙，播放鼓掌动作并送你一张随机表情">
        <span className="mascot-portrait" aria-hidden="true">
          <img
            className="mascot-sheet"
            src={SHEET}
            alt=""
            draggable={false}
            style={{ transform: spriteTransform }}
          />
        </span>
        <span className="mascot-name">{peek ? '点我出来' : (bubble?.name || ACTION_LABEL[action])}</span>
      </button>
    </div>
  );
}
