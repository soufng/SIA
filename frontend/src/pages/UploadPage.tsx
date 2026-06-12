import { useNavigate } from "react-router-dom";
import { Upload as UploadIcon } from "lucide-react";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { useAnalysisStore } from "@/store/analysis";
import { ResultsPage } from "./ResultsPage";

// L'upload se fait depuis la page d'accueil (HomePage → UploadForm).
// Cette route ne sert qu'à afficher le rapport d'une analyse :
//   - soit l'analyse qui vient d'être lancée depuis l'accueil
//   - soit une analyse ouverte depuis l'historique
// Si le store est vide, on invite l'utilisateur à retourner à l'accueil.
export function UploadPage() {
  const analysis = useAnalysisStore((s) => s.analysis);
  const navigate = useNavigate();

  if (!analysis) {
    return (
      <div className="space-y-4">
        <h1 className="text-3xl font-bold text-ccm-ink">
          Résultats de l'analyse
        </h1>
        <Alert variant="info">
          Aucun rapport à afficher pour l'instant. Lancez une analyse depuis
          l'accueil ou ouvrez un rapport depuis l'historique.
        </Alert>
        <div className="flex gap-2">
          <Button onClick={() => navigate("/")}>
            <UploadIcon className="h-4 w-4" />
            Aller à l'accueil pour analyser un PDF
          </Button>
          <Button variant="outline" onClick={() => navigate("/history")}>
            Ouvrir l'historique
          </Button>
        </div>
      </div>
    );
  }

  return <ResultsPage />;
}
