export async function onRequestPost(context) {
  const { request, env } = context;

  // 1. 브라우저가 보낸 바디 데이터(keyword, page 등) 읽기
  const requestBody = await request.text();

  // 2. Cloudflare Pages 대시보드에 입력해 둔 환경변수 꺼내기
  // - env.RENDER_BACKEND_URL 예시: https://your-backend.onrender.com
  // - env.BACKEND_SECRET_KEY 예시: hyunjae-super-secret-key-1234
  const targetBackend = env.RENDER_BACKEND_URL;
  const secretKey = env.BACKEND_SECRET_KEY;

  if (!targetBackend || !secretKey) {
    return new Response(
      JSON.stringify({ detail: "Cloudflare Pages 환경변수(RENDER_BACKEND_URL 또는 BACKEND_SECRET_KEY) 설정이 누락되었습니다." }),
      { status: 500, headers: { "Content-Type": "application/json" } }
    );
  }

  // 진짜 Render 백엔드 주소 조립
  const backendUrl = `${targetBackend.replace(/\/$/, "")}/crawl`;

  try {
    // 3. 진짜 Render 백엔드로 안전하게 대리 요청 전송
    const response = await fetch(backendUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-API-Key": secretKey, // ◀ 사용자는 절대 볼 수 없는 서버 안에서의 보안 키 탑재!
      },
      body: requestBody,
    });

    const responseData = await response.text();

    // 4. 결과를 Pages 사이트로 반환
    return new Response(responseData, {
      status: response.status,
      headers: {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "https://issue-tracker.hyunjae.co.kr",
      },
    });
  } catch (error) {
    return new Response(
      JSON.stringify({ detail: `Render 백엔드 서버 연결 실패: ${error.message}` }),
      { status: 502, headers: { "Content-Type": "application/json" } }
    );
  }
}