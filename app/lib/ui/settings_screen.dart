import 'package:flutter/material.dart';

import '../app_model.dart';
import '../core/preprocess.dart';

class SettingsScreen extends StatelessWidget {
  const SettingsScreen({super.key, required this.model});
  final AppModel model;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: model,
      builder: (context, _) => Scaffold(
        appBar: AppBar(title: const Text('설정')),
        body: ListView(children: [
          ListTile(
            title: Text('인식 기준 ${(model.threshold * 100).toStringAsFixed(0)}%'),
            subtitle: Slider(
              value: model.threshold,
              min: 0.3,
              max: 0.95,
              divisions: 13,
              onChanged: model.setThreshold,
            ),
          ),
          const ListTile(
              dense: true,
              title: Text('높이면 오인식이 줄고, 낮추면 더 잘 찾지만 엉뚱한 것도 찾아요. 기준 아래거나 "포켓몬 아님"이면 찾지 못했다고 해요. (기본 0.5 는 학습 데이터 test 기준 임시값이에요. 실제 촬영으로 다시 정할 예정)')),
          if (model.hasFormModel) ...[
            ListTile(
              title: Text('폼 판정 기준 ${(model.formThreshold * 100).toStringAsFixed(0)}%'),
              subtitle: Slider(value: model.formThreshold, min: 0.3, max: 0.95, divisions: 13, onChanged: model.setFormThreshold),
            ),
            const ListTile(dense: true, title: Text('폼(리전폼·메가 등) 확신도가 이보다 낮으면 기본 모습으로 보여 줘요. 0.6 은 임시값이에요.')),
          ],
          ListTile(
            title: const Text('이미지 맞추기'),
            trailing: SegmentedButton<CropMode>(
              showSelectedIcon: false,
              segments: [for (final c in CropMode.values) ButtonSegment(value: c, label: Text(c.label))],
              selected: {model.cropMode},
              onSelectionChanged: (s) => model.setCropMode(s.first),
            ),
          ),
          ListTile(
            title: const Text('카메라 해상도'),
            subtitle: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('높을수록 사진을 확대해도 선명하고 작은 포켓몬을 잘 찾지만, 영상 인식은 느려질 수 있어요. 바꾸면 카메라를 다시 열어요.'
                  '${model.camActualSize != null ? ' (지금 ${model.camActualSize})' : ''}'),
              const SizedBox(height: 8),
              SegmentedButton<CamRes>(
                showSelectedIcon: false,
                segments: [
                  for (final c in model.availableResolutions)
                    ButtonSegment(
                      value: c,
                      label: Text(
                        '${c.label}\n${model.resSizes[c.name] ?? '미확인'}',
                        textAlign: TextAlign.center,
                        style: const TextStyle(fontSize: 12, height: 1.2),
                      ),
                    ),
                ],
                selected: {model.availableResolutions.contains(model.camRes) ? model.camRes : model.availableResolutions.first},
                onSelectionChanged: model.probing ? null : (s) => model.setCamRes(s.first),
              ),
              const SizedBox(height: 6),
              if (model.hwKnown && model.hwMaxPhoto != null)
                Text('이 폰 카메라가 하드웨어로 알려 준 사진 최대 크기: ${model.hwMaxPhoto}', style: const TextStyle(fontSize: 12)),
              if (model.probing)
                const Row(children: [
                  SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2)),
                  SizedBox(width: 8),
                  Text('이 기기가 지원하는 해상도를 확인하는 중… (몇 초)', style: TextStyle(fontSize: 12)),
                ])
              else
                TextButton.icon(
                  style: TextButton.styleFrom(visualDensity: VisualDensity.compact, padding: EdgeInsets.zero),
                  onPressed: model.hasSpeciesModel ? model.probeResolutions : null,
                  icon: const Icon(Icons.search, size: 18),
                  label: Text(model.hwKnown ? '카메라를 열어서 한 번 더 확인' : (model.resSizes.isEmpty ? '이 기기가 지원하는 해상도 확인' : '다시 확인')),
                ),
            ]),
          ),
          SwitchListTile(
              title: const Text('여러 마리 찾기'),
              subtitle: const Text('화면 전체를 겹치는 7곳으로 나눠 찾고, 여러 마리를 동시에 보여 줘요. 끄면 가운데 한 곳만 봐요 (더 빠름). 느려지거나 엉뚱한 곳을 찾을 수 있어요.'),
              value: model.multi,
              onChanged: model.setMulti),
          SwitchListTile(title: const Text('인식하면 음성 자동 재생'), value: model.autoPlay, onChanged: model.setAutoPlay),
          SwitchListTile(
              title: const Text('진단 보기'),
              subtitle: const Text('모델에 들어가는 이미지와 상위 3개 후보를 카메라 화면에 표시'),
              value: model.showDebug,
              onChanged: model.setShowDebug),
          ListTile(
            title: const Text('모델'),
            subtitle: Text([
              model.hasSpeciesModel
                  ? '① 종 모델 ${model.speciesClasses}클래스, 입력 ${model.inputSize}px'
                  : '① 종 모델 없음 (인식 안 됨)',
              model.hasFormModel ? '② 폼 모델 ${model.formClasses}클래스' : '② 폼 모델 없음 (종만 판정)',
              if (model.modelError != null) '오류: ${model.modelError}',
            ].join('\n')),
          ),
          ListTile(
            title: const Text('도감 초기화'),
            subtitle: Text('${model.discovered.length}종 발견'),
            onTap: () async {
              final ok = await showDialog<bool>(
                context: context,
                builder: (c) => AlertDialog(
                  title: const Text('도감을 초기화할까요?'),
                  actions: [
                    TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('취소')),
                    TextButton(onPressed: () => Navigator.pop(c, true), child: const Text('초기화')),
                  ],
                ),
              );
              if (ok == true) model.resetDex();
            },
          ),
        ]),
      ),
    );
  }
}
