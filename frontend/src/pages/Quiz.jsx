import React, { useState } from 'react';
import { generateQuiz } from '../services/api';
import useIsMobile from '../hooks/useIsMobile';

const WRONG_KEY = 'pesticide_quiz_wrong_questions';

const CATEGORIES = [
  { value: 'all', label: '綜合（全部分類）' },
  { value: 'law', label: '農藥法規' },
  { value: 'pest', label: '植物保護' },
  { value: 'safety', label: '安全採收期' },
  { value: 'term', label: '名詞與定義' },
];

const COUNTS = [10, 20, 30];

const WARM = {
  page: '#F5FAF7',
  surface: '#FFFFFF',
  border: '#CFE0D5',
  accent: '#0F4A34',
  accentLight: '#DCEAE2',
  accentText: '#E6F1EC',
  textDark: '#16241C',
  textMuted: '#3D4A43',
  gold: '#0F4A34',
  correctBg: '#DCFCE7', correctBorder: '#16A34A', correctText: '#166534',
  wrongBg: '#FEE2E2', wrongBorder: '#DC2626', wrongText: '#991B1B',
  wrongPanelBg: '#FEF2F2', wrongPanelBorder: '#FCA5A5',
};

function loadWrong() {
  try { return JSON.parse(localStorage.getItem(WRONG_KEY) || '[]'); } catch { return []; }
}
function saveWrong(list) {
  try { localStorage.setItem(WRONG_KEY, JSON.stringify(list)); } catch {}
}
function upsertWrong(existing, entry) {
  const idx = existing.findIndex(w => w.question === entry.question);
  const next = [...existing];
  if (idx >= 0) next[idx] = entry; else next.push(entry);
  return next;
}
function removeCorrected(existing, question) {
  return existing.filter(w => w.question !== question);
}

export default function Quiz() {
  const [screen, setScreen] = useState('settings');
  const [category, setCategory] = useState('all');
  const [count, setCount] = useState(10);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const isMobile = useIsMobile();

  const [questions, setQuestions] = useState([]);
  const [idx, setIdx] = useState(0);
  const [selected, setSelected] = useState(null);
  const [records, setRecords] = useState([]);

  const [wrongList, setWrongList] = useState(loadWrong());

  const pad = isMobile ? '28px 16px' : '40px 24px';

  const startQuiz = async (customQuestions) => {
    setError('');
    if (customQuestions) {
      setQuestions(customQuestions);
      setIdx(0); setSelected(null); setRecords([]);
      setScreen('quiz');
      return;
    }
    setLoading(true);
    try {
      const res = await generateQuiz(category, count);
      const qs = res.data['題目'] || [];
      if (qs.length === 0) { setError('目前資料不足以產生題目，請換個分類試試。'); return; }
      setQuestions(qs);
      setIdx(0); setSelected(null); setRecords([]);
      setScreen('quiz');
    } catch (e) {
      setError('題目載入失敗，請確認後端是否正在運行。');
    } finally {
      setLoading(false);
    }
  };

  const selectOption = (i) => setSelected(i);

  const finishQuiz = (finalRecords) => {
    let updated = loadWrong();
    finalRecords.forEach(r => {
      if (r.selected_index !== r.answer_index) updated = upsertWrong(updated, r);
      else updated = removeCorrected(updated, r.question);
    });
    saveWrong(updated);
    setWrongList(updated);
    setScreen('result');
  };

  const nextQuestion = () => {
    const q = questions[idx];
    const record = {
      question: q.question, options: q.options, answer_index: q.answer_index,
      selected_index: selected, category: q.category, explanation: q.explanation, source: q.source,
    };
    const newRecords = [...records, record];
    setRecords(newRecords);
    if (idx + 1 < questions.length) { setIdx(idx + 1); setSelected(null); }
    else finishQuiz(newRecords);
  };

  const score = records.filter(r => r.selected_index === r.answer_index).length;

  const practiceWrong = () => {
    if (wrongList.length === 0) return;
    const asQuestions = wrongList.map(w => ({
      category: w.category, question: w.question, options: w.options,
      answer_index: w.answer_index, explanation: w.explanation, source: w.source,
    }));
    startQuiz(asQuestions);
  };

  const clearWrong = () => { saveWrong([]); setWrongList([]); };
  const backToSettings = () => setScreen('settings');

  if (screen === 'settings') {
    return (
      <div style={{ maxWidth: 560, margin: '0 auto', padding: pad, background: WARM.page }}>
        <h1 style={{ fontSize: isMobile ? 24 : 28, fontWeight: 700, color: WARM.accent, marginBottom: 8 }}>📝 農藥知識模擬考</h1>
        <p style={{ color: WARM.textMuted, fontSize: isMobile ? 14 : 15, marginBottom: 30 }}>題目由系統即時從法規、農藥登記與問答資料庫自動產生</p>

        <div style={{ background: WARM.surface, border: `1px solid ${WARM.border}`, borderRadius: 14, padding: isMobile ? 20 : 26, marginBottom: 22 }}>
          <div style={{ marginBottom: 22 }}>
            <div style={{ fontWeight: 600, color: WARM.accent, fontSize: 15, marginBottom: 12 }}>題目分類</div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 9 }}>
              {CATEGORIES.map(c => (
                <button key={c.value} onClick={() => setCategory(c.value)}
                  style={{
                    padding: '9px 16px', borderRadius: 20, fontSize: 14, cursor: 'pointer',
                    border: category === c.value ? `1.5px solid ${WARM.accent}` : `1.5px solid ${WARM.border}`,
                    background: category === c.value ? WARM.accent : WARM.surface,
                    color: category === c.value ? WARM.accentText : WARM.textDark, fontWeight: 500,
                  }}>
                  {c.label}
                </button>
              ))}
            </div>
          </div>
          <div>
            <div style={{ fontWeight: 600, color: WARM.accent, fontSize: 15, marginBottom: 12 }}>題數</div>
            <div style={{ display: 'flex', gap: 9 }}>
              {COUNTS.map(n => (
                <button key={n} onClick={() => setCount(n)}
                  style={{
                    padding: '9px 20px', borderRadius: 20, fontSize: 14, cursor: 'pointer',
                    border: count === n ? `1.5px solid ${WARM.accent}` : `1.5px solid ${WARM.border}`,
                    background: count === n ? WARM.accent : WARM.surface,
                    color: count === n ? WARM.accentText : WARM.textDark, fontWeight: 500,
                  }}>
                  {n} 題
                </button>
              ))}
            </div>
          </div>
        </div>

        {error && <div style={{ color: WARM.wrongText, fontSize: 14, marginBottom: 16 }}>{error}</div>}

        <button onClick={() => startQuiz()} disabled={loading}
          style={{ width: '100%', padding: '15px 0', background: WARM.accent, color: WARM.accentText, border: 'none', borderRadius: 10, fontWeight: 700, fontSize: 16, cursor: 'pointer', marginBottom: 14 }}>
          {loading ? '出題中…' : '開始測驗'}
        </button>

        {wrongList.length > 0 && (
          <button onClick={() => setScreen('wrong')}
            style={{ width: '100%', padding: '13px 0', background: WARM.wrongPanelBg, color: WARM.wrongText, border: `1.5px solid ${WARM.wrongPanelBorder}`, borderRadius: 10, fontWeight: 600, fontSize: 15, cursor: 'pointer' }}>
            查看錯題整理（{wrongList.length} 題）
          </button>
        )}
      </div>
    );
  }

  if (screen === 'quiz') {
    const q = questions[idx];
    return (
      <div style={{ maxWidth: 640, margin: '0 auto', padding: isMobile ? '24px 16px' : '34px 24px', background: WARM.page }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
          <span style={{ fontSize: 14, color: WARM.textMuted }}>第 {idx + 1} / {questions.length} 題</span>
          <span style={{ fontSize: 13, background: WARM.accentLight, color: WARM.accent, padding: '4px 12px', borderRadius: 10, fontWeight: 600 }}>{q.category}</span>
        </div>
        <div style={{ height: 7, background: WARM.accentLight, borderRadius: 4, marginBottom: 26, overflow: 'hidden' }}>
          <div style={{ height: '100%', width: `${((idx) / questions.length) * 100}%`, background: WARM.gold, transition: 'width 0.2s' }} />
        </div>

        <div style={{ background: WARM.surface, border: `1px solid ${WARM.border}`, borderRadius: 14, padding: isMobile ? 20 : 26 }}>
          <p style={{ fontSize: isMobile ? 17 : 18, fontWeight: 600, color: WARM.textDark, marginBottom: 22, lineHeight: 1.7 }}>{q.question}</p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 11 }}>
            {q.options.map((opt, i) => {
              const isSelected = i === selected;
              return (
                <button key={i} onClick={() => selectOption(i)}
                  style={{
                    textAlign: 'left', padding: '13px 18px', borderRadius: 10,
                    border: isSelected ? `1.5px solid ${WARM.accent}` : `1.5px solid ${WARM.border}`,
                    background: isSelected ? WARM.accentLight : WARM.surface,
                    color: WARM.textDark, fontSize: 16, lineHeight: 1.6, cursor: 'pointer',
                  }}>
                  {opt}
                </button>
              );
            })}
          </div>
          <button onClick={nextQuestion} disabled={selected === null}
            style={{
              marginTop: 20, width: '100%', padding: '13px 0',
              background: selected === null ? WARM.accentLight : WARM.accent,
              color: selected === null ? WARM.textMuted : WARM.accentText,
              border: 'none', borderRadius: 10, fontWeight: 700, fontSize: 16,
              cursor: selected === null ? 'not-allowed' : 'pointer',
            }}>
            {idx + 1 < questions.length ? '下一題' : '交卷'}
          </button>
        </div>
      </div>
    );
  }

  if (screen === 'result') {
    const pct = Math.round((score / questions.length) * 100);
    return (
      <div style={{ maxWidth: 640, margin: '0 auto', padding: isMobile ? '30px 16px' : '42px 24px', background: WARM.page }}>
        <div style={{ textAlign: 'center', marginBottom: 34 }}>
          <div style={{
            display: 'inline-flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
            width: 148, height: 148, borderRadius: '50%', border: `5px solid ${WARM.gold}`, color: WARM.accent,
            transform: 'rotate(-6deg)', marginBottom: 18,
          }}>
            <span style={{ fontSize: 38, fontWeight: 800 }}>{pct}</span>
            <span style={{ fontSize: 14, fontWeight: 700 }}>分</span>
          </div>
          <div style={{ fontSize: 18, color: WARM.textDark, fontWeight: 600 }}>答對 {score} / {questions.length} 題</div>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 11, marginBottom: 26 }}>
          {records.map((r, i) => {
            const correct = r.selected_index === r.answer_index;
            return (
              <div key={i} style={{ background: WARM.surface, border: `1px solid ${correct ? WARM.border : WARM.wrongPanelBorder}`, borderRadius: 10, padding: '14px 18px' }}>
                <div style={{ fontSize: 15, color: WARM.textDark, marginBottom: 5 }}>
                  {correct ? '✅' : '❌'} {i + 1}. {r.question}
                </div>
                <div style={{ fontSize: 14, color: WARM.textMuted }}>
                  你的答案：<span style={{ color: correct ? WARM.correctBorder : WARM.wrongBorder }}>{r.options[r.selected_index]}</span>
                  {!correct && <>　正解：<span style={{ color: WARM.correctBorder }}>{r.options[r.answer_index]}</span></>}
                </div>
                {r.explanation && (
                  <div style={{ marginTop: 7, fontSize: 14, color: WARM.textMuted, lineHeight: 1.65 }}>
                    <strong style={{ color: WARM.accent }}>解說：</strong>{r.explanation}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        <div style={{ display: 'flex', gap: 11, flexDirection: isMobile ? 'column' : 'row' }}>
          <button onClick={backToSettings} style={{ flex: 1, padding: '13px 0', background: WARM.surface, border: `1.5px solid ${WARM.border}`, color: WARM.accent, borderRadius: 10, fontWeight: 600, fontSize: 15, cursor: 'pointer' }}>
            返回設定畫面
          </button>
          {wrongList.length > 0 && (
            <button onClick={() => setScreen('wrong')} style={{ flex: 1, padding: '13px 0', background: WARM.wrongPanelBg, color: WARM.wrongText, border: `1.5px solid ${WARM.wrongPanelBorder}`, borderRadius: 10, fontWeight: 600, fontSize: 15, cursor: 'pointer' }}>
              查看錯題整理（{wrongList.length}）
            </button>
          )}
        </div>
      </div>
    );
  }

  if (screen === 'wrong') {
    return (
      <div style={{ maxWidth: 640, margin: '0 auto', padding: isMobile ? '30px 16px' : '42px 24px', background: WARM.page }}>
        <h1 style={{ fontSize: 24, fontWeight: 700, color: WARM.accent, marginBottom: 8 }}>❌ 錯題整理</h1>
        <p style={{ color: WARM.textMuted, fontSize: 15, marginBottom: 22 }}>累積 {wrongList.length} 題，答對後會自動從清單移除</p>

        {wrongList.length === 0 ? (
          <p style={{ color: WARM.textMuted }}>目前沒有錯題，太厲害了！</p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 13, marginBottom: 26 }}>
            {wrongList.map((w, i) => (
              <div key={i} style={{ background: WARM.surface, border: `1px solid ${WARM.border}`, borderRadius: 10, padding: '16px 18px' }}>
                <div style={{ fontSize: 12, background: WARM.accentLight, color: WARM.accent, padding: '3px 9px', borderRadius: 8, display: 'inline-block', marginBottom: 9, fontWeight: 600 }}>{w.category}</div>
                <div style={{ fontSize: 16, fontWeight: 600, color: WARM.textDark, marginBottom: 11 }}>{w.question}</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
                  {w.options.map((opt, j) => {
                    const isAnswer = j === w.answer_index;
                    const isSelected = j === w.selected_index;
                    let label = null;
                    if (isAnswer) label = <span style={{ color: WARM.correctBorder, fontWeight: 700 }}> ← 正解</span>;
                    else if (isSelected) label = <span style={{ color: WARM.wrongBorder, fontWeight: 700 }}> ← 你的答案</span>;
                    return (
                      <div key={j} style={{ fontSize: 14, padding: '7px 11px', borderRadius: 6, background: isAnswer ? WARM.correctBg : isSelected ? WARM.wrongBg : WARM.accentLight, color: WARM.textDark }}>
                        {opt}{label}
                      </div>
                    );
                  })}
                </div>
                <div style={{ marginTop: 11, fontSize: 13, color: WARM.textMuted, lineHeight: 1.65 }}>
                  <strong style={{ color: WARM.accent }}>解說：</strong>{w.explanation}
                </div>
              </div>
            ))}
          </div>
        )}

        <div style={{ display: 'flex', gap: 11, flexDirection: isMobile ? 'column' : 'row' }}>
          <button onClick={backToSettings} style={{ flex: 1, padding: '13px 0', background: WARM.surface, border: `1.5px solid ${WARM.border}`, color: WARM.accent, borderRadius: 10, fontWeight: 600, fontSize: 15, cursor: 'pointer' }}>
            返回設定畫面
          </button>
          {wrongList.length > 0 && (
            <>
              <button onClick={practiceWrong} style={{ flex: 1, padding: '13px 0', background: WARM.accent, color: WARM.accentText, border: 'none', borderRadius: 10, fontWeight: 600, fontSize: 15, cursor: 'pointer' }}>
                重新練習全部錯題
              </button>
              <button onClick={clearWrong} style={{ flex: 1, padding: '13px 0', background: WARM.surface, border: `1.5px solid ${WARM.wrongPanelBorder}`, color: WARM.wrongText, borderRadius: 10, fontWeight: 600, fontSize: 15, cursor: 'pointer' }}>
                清空錯題本
              </button>
            </>
          )}
        </div>
      </div>
    );
  }

  return null;
}