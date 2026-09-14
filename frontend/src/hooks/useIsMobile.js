import { useState, useEffect } from 'react';

// 全站共用的響應式判斷：視窗寬度 <= 768px 視為手機版。
// 各頁面用 const isMobile = useIsMobile(); 來切換版面。
export default function useIsMobile(breakpoint = 768) {
  const [isMobile, setIsMobile] = useState(
    typeof window !== 'undefined' ? window.innerWidth <= breakpoint : false
  );

  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth <= breakpoint);
    window.addEventListener('resize', onResize);
    onResize();
    return () => window.removeEventListener('resize', onResize);
  }, [breakpoint]);

  return isMobile;
}