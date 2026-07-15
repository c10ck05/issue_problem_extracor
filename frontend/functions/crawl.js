export async function onRequestPost(context) {
  const { request, env } = context;

  const requestBody = await request.text();

  const targetBackend = env.RENDER_BACKEND_URL;
  const secretKey = env.BACKEND_SECRET_KEY;

  if (!targetBackend || !secretKey) {
    return new Response(
      JSON.stringify({ detail: "Cloudflare Pages 환경변수(RENDER_BACKEND_URL 또는 BACKEND_SECRET_KEY) 설정이 누락되었습니다." }),
      { status: 500, headers: { "Content-Type": "application/json" } }
    );
  }

  const cleanBase = targetBackend.trim().replace(/\/+$/, "");
  const backendUrl = `${cleanBase}/crawl`;

  try {
    const response = await fetch(backendUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-API-Key": secretKey,
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