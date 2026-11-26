export type GuideMethod = { title: string; text: string };

export const guideSteps: {
  title: string;
  text: string;
  detail: string;
  methods?: GuideMethod[];
}[] = [
  {
    title: "건물과 공간 등록",
    text: "건물과 공간 이름, 공간 크기, 창문 크기와 위치를 입력하세요.",
    detail: "창문이 있는 벽과 창문 둘레 여백(벽 끝, 천장, 바닥까지 거리)을 입력하세요. 필름 구성은 미리보기에서 확인하세요.",
  },
  {
    title: "QR 측정 준비",
    text: "마커를 띄울 필름 칸을 고르세요.",
    detail: "촬영 시작을 누르면 고른 필름 칸에 마커가 떠요. 웹에서는 앱 연결 링크를 휴대폰에서 열어 촬영을 이어가세요.",
  },
  {
    title: "가구 촬영",
    text: "가구를 촬영해 크기와 위치를 정하세요.",
    methods: [
      {
        title: "사진으로 가구 찾기",
        text: "창문 마커와 가구가 같이 나오게 방을 한 장 찍으세요. 가구를 찾아 크기와 위치의 초안을 만들어 줘요. 빠르게 시작할 때 좋고, 결과는 도면에서 고칠 수 있어요.",
      },
      {
        title: "마커로 정밀 촬영",
        text: "상판 형태를 고르고 P1부터 차례로 이동하며 지점마다 한 번씩 찍으세요. 사진마다 창문 마커 전체가 보여야 해요. 정확한 치수가 필요할 때 쓰세요.",
      },
    ],
    detail: "마커가 잘리거나 사진이 흔들리면 같은 지점에서 다시 찍으세요. 다른 촬영이 진행 중이라고 나오면 이전 촬영 끝내기를 누르세요.",
  },
  {
    title: "도면과 치수 확인",
    text: "2D 도면에서 가구를 끌어 옮기고, 가로·세로·높이를 실제 치수에 맞게 고치세요.",
    detail: "치수는 m 단위예요. 가구는 사진으로 찾기, 마커 촬영, 직접 입력 중에서 골라 추가하세요.",
  },
  {
    title: "보호 영역과 등급 설정",
    text: "가구를 선택하거나 도면에 보호 영역을 그리고, 보호할 높이와 A~D 등급을 정하세요.",
    detail: "보호 영역은 앱과 웹에서 편집하고 저장할 수 있어요. 넓은 도면을 보며 고치려면 웹에서 같은 공간을 여세요.",
  },
  {
    title: "햇빛 확인과 차단 시간 설정",
    text: "시간별 햇빛과 필름 칸 상태를 확인하고, 빛을 막을 시간을 정하세요.",
    detail: "차단 시간 설정을 켜고 시작·종료 시간을 정하세요. 정한 시간 안에 햇빛이 보호 영역에 닿으면 해당 필름 칸을 가려요. 설정을 끄면 필름을 가리지 않아요.",
  },
];

// Reference pages shown after the numbered steps (web guide) and under 더보기 (app). `art` is the GuideIllustration index.
export const guideExtras: {
  title: string;
  art: number;
  text: string;
  detail?: string;
  grades?: boolean;
}[] = [
  {
    title: "등급 기준",
    art: 6,
    text: "가구마다 햇빛을 막는 정도를 등급으로 정해요.",
    grades: true,
  },
  {
    title: "차광 필름",
    art: 7,
    text: "창문의 필름은 여러 칸으로 나뉘어 있고, 칸마다 따로 어두워져요.",
    detail: "가구에 닿는 햇빛이 지나는 칸만 닫아요. 나머지 칸은 투명해서 방은 밝게 유지돼요.",
  },
];
