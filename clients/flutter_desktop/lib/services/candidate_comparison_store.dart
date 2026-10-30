import '../models/epub_candidate.dart';
import 'evaluation_store.dart';

/// Persistence/decoding boundary for unverified candidate comparison.
///
/// Candidate evaluations are saved beside the source as a human-only sidecar;
/// they are deliberately not project decisions and never enter verified export.
abstract interface class CandidateComparisonStore {
  Future<EpubCandidate> loadCandidate(String path);

  Future<Map<String, dynamic>?> readEvaluation(String sourcePdfPath);

  Future<String> fingerprint(String path);

  Future<String> saveEvaluation({
    required String sourcePdfPath,
    required String candidateAPath,
    required String candidateBPath,
    required String candidateALabel,
    required String candidateBLabel,
    required Map<int, String> pageDecisions,
    required String? overallWinner,
    required String notes,
    required String expectedSourceSha256,
    required String expectedCandidateASha256,
    required String expectedCandidateBSha256,
  });
}

class FileCandidateComparisonStore implements CandidateComparisonStore {
  const FileCandidateComparisonStore();

  @override
  Future<EpubCandidate> loadCandidate(String path) => EpubCandidate.load(path);

  @override
  Future<Map<String, dynamic>?> readEvaluation(String sourcePdfPath) =>
      EvaluationStore.read(sourcePdfPath);

  @override
  Future<String> fingerprint(String path) => EvaluationStore.fingerprint(path);

  @override
  Future<String> saveEvaluation({
    required String sourcePdfPath,
    required String candidateAPath,
    required String candidateBPath,
    required String candidateALabel,
    required String candidateBLabel,
    required Map<int, String> pageDecisions,
    required String? overallWinner,
    required String notes,
    required String expectedSourceSha256,
    required String expectedCandidateASha256,
    required String expectedCandidateBSha256,
  }) =>
      EvaluationStore.save(
        sourcePdfPath: sourcePdfPath,
        candidateAPath: candidateAPath,
        candidateBPath: candidateBPath,
        candidateALabel: candidateALabel,
        candidateBLabel: candidateBLabel,
        pageDecisions: pageDecisions,
        overallWinner: overallWinner,
        notes: notes,
        expectedSourceSha256: expectedSourceSha256,
        expectedCandidateASha256: expectedCandidateASha256,
        expectedCandidateBSha256: expectedCandidateBSha256,
      );
}
