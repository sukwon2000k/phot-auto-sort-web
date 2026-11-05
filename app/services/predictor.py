"""학습한 EfficientNetV2-L로 사진을 food, landscape, person 중 하나로 분류합니다.

터미널에서 직접 실행하거나, 나중에 폴더 분류와 FastAPI에서 가져와 사용합니다.
이 파일에서는 사진을 이동·수정하거나 모델을 학습하지 않습니다.
"""

import argparse
from contextlib import nullcontext
from pathlib import Path

import torch
from PIL import Image, ImageOps
from torch import nn
from torchvision.models import EfficientNet_V2_L_Weights, efficientnet_v2_l


class PhotoPredictor:
    """저장된 모델을 한 번 불러와 여러 사진의 예측에 재사용합니다."""

    def __init__(self, checkpoint_path: str | Path, device: str = "auto"):
        """가중치와 클래스 정보, 평가 전처리를 준비합니다.

        입력:
            checkpoint_path: 노트북에서 저장한 best_head.pth 경로
            device: auto(GPU 우선), cpu, cuda 중 하나
        """
        checkpoint_path = Path(checkpoint_path).expanduser().resolve()
        if not checkpoint_path.is_file():
            raise FileNotFoundError(f"모델 파일을 찾지 못했습니다: {checkpoint_path}")
        if device not in {"auto", "cpu", "cuda"}:
            raise ValueError("device는 auto, cpu, cuda 중 하나여야 합니다.")

        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA를 사용할 수 없습니다. Python 환경을 확인하세요.")
        self.device = torch.device(device)

        # 학습 코드에서 저장한 텐서와 설정을 CPU로 읽습니다.
        checkpoint = torch.load(
            checkpoint_path, map_location="cpu", weights_only=True
        )
        if checkpoint.get("architecture") != "efficientnet_v2_l":
            raise ValueError("EfficientNetV2-L 체크포인트가 아닙니다.")

        self.class_to_idx = checkpoint["class_to_idx"]
        expected_classes = {"food": 0, "landscape": 1, "person": 2}
        if self.class_to_idx != expected_classes:
            raise ValueError("체크포인트의 클래스 정보가 프로젝트 설정과 다릅니다.")
        self.idx_to_class = {
            index: name for name, index in self.class_to_idx.items()
        }

        # 가중치 종류의 전처리 설정만 사용하므로 다운로드가 발생하지 않습니다.
        weights = EfficientNet_V2_L_Weights[checkpoint["pretrained_weights"]]
        self.transform = weights.transforms()
        if (
            checkpoint["image_size"] != self.transform.crop_size[0]
            or list(checkpoint["mean"]) != list(self.transform.mean)
            or list(checkpoint["std"]) != list(self.transform.std)
        ):
            raise ValueError("저장된 전처리 정보가 가중치의 평가 설정과 다릅니다.")

        # weights=None으로 구조만 생성하고 우리가 학습한 가중치를 넣습니다.
        model = efficientnet_v2_l(weights=None)
        model.classifier[1] = nn.Linear(
            model.classifier[1].in_features, len(self.class_to_idx)
        )
        model.load_state_dict(checkpoint["model_state_dict"], strict=True)
        self.epoch = int(checkpoint["epoch"])
        del checkpoint

        self.model = model.to(self.device)
        self.model.eval()

    def predict_image(self, image: Image.Image) -> str:
        """Pillow 이미지 한 장을 받아 예측 클래스 이름을 반환합니다.

        나중에 웹에서 받은 이미지에도 사용할 공통 예측 함수입니다.
        반환값은 food, landscape, person 중 하나입니다.
        """
        # 휴대폰 사진의 EXIF 회전 정보를 반영하고 RGB 3채널로 맞춥니다.
        # 메모리 안에서만 처리하므로 원본 파일은 바뀌지 않습니다.
        rgb_image = ImageOps.exif_transpose(image).convert("RGB")
        tensor = self.transform(rgb_image).unsqueeze(0).to(self.device)

        # GPU에서는 기존 평가와 같이 혼합 정밀도를 사용합니다.
        # CPU 배포에서는 기본 float32로 실행합니다.
        amp_context = (
            torch.autocast(device_type="cuda", dtype=torch.float16)
            if self.device.type == "cuda"
            else nullcontext()
        )
        with torch.inference_mode(), amp_context:
            outputs = self.model(tensor)

        if not torch.isfinite(outputs).all().item():
            raise RuntimeError("예측 점수에 NaN 또는 무한대가 있습니다.")

        # 확인 필요 기준 없이 가장 점수가 높은 클래스 하나를 선택합니다.
        predicted_index = outputs.argmax(dim=1).item()
        return self.idx_to_class[predicted_index]

    def predict_path(self, image_path: str | Path) -> str:
        """사진 파일 경로를 받아 예측 클래스 이름을 반환합니다.

        파일이 없거나 이미지가 손상되었다면 예외를 호출한 코드로 전달합니다.
        """
        image_path = Path(image_path).expanduser().resolve()
        if not image_path.is_file():
            raise FileNotFoundError(f"사진을 찾지 못했습니다: {image_path}")
        with Image.open(image_path) as image:
            return self.predict_image(image)


def main() -> None:
    """터미널 인자로 지정한 모델과 사진으로 한 장 예측을 실행합니다."""
    parser = argparse.ArgumentParser(description="사진 한 장을 3종으로 분류합니다.")
    parser.add_argument("--checkpoint", required=True, help="best_head.pth 경로")
    parser.add_argument("--image", required=True, help="분류할 사진 경로")
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    args = parser.parse_args()

    predictor = PhotoPredictor(args.checkpoint, device=args.device)
    print("사용 장치:", predictor.device)
    print("저장된 에포크:", predictor.epoch)
    print("분류 결과:", predictor.predict_path(args.image))


# 다른 파일에서 import할 때는 실행하지 않고, 직접 실행할 때만 동작합니다.
if __name__ == "__main__":
    main()

