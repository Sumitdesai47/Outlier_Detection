import pandas as pd
import numpy as np
from sklearn.ensemble import IsolationForest


# -------------------------------
# Robust Z-score (MAD)
# -------------------------------
def robust_zscore(series):
    s = pd.to_numeric(series, errors="coerce")
    med = s.median()
    mad = np.median(np.abs(s - med))

    if mad == 0:
        std = s.std()
        return (s - med) / (std if std > 0 else 1)

    return (s - med) / (1.4826 * mad)


# -------------------------------
# Detect flatline (sensor stuck)
# -------------------------------
def detect_flatline(series, window=5):
    return series.rolling(window).std() == 0


# -------------------------------
# Detect sudden jump
# -------------------------------
def detect_jump(series, threshold=3):
    diff = series.diff().abs()
    return diff > threshold * diff.std()


# -------------------------------
# Main function
# -------------------------------
def detect_outliers(df, timestamp_col="Timestamp"):

    results = []

    for col in df.columns:
        if col == timestamp_col:
            continue

        series = pd.to_numeric(df[col], errors="coerce")

        # ---- Robust Z-score ----
        z = robust_zscore(series)

        # ---- Flatline ----
        flat = detect_flatline(series)

        # ---- Jump ----
        jump = detect_jump(series)

        # ---- Isolation Forest (multivariate optional) ----
        valid_idx = series.dropna().index

        iso_flag = pd.Series(0, index=series.index)
        if len(valid_idx) > 10:
            # Keep dashboard runs fast on wide files: small forest + subsample rows.
            n = len(valid_idx)
            max_samples = min(256, n)
            iso = IsolationForest(
                contamination=0.02,
                random_state=42,
                n_estimators=50,
                max_samples=max_samples,
            )
            reshaped = series.loc[valid_idx].values.reshape(-1, 1)
            iso_pred = iso.fit_predict(reshaped)
            iso_flag.loc[valid_idx] = iso_pred

        # ---- Classification ----
        for i in range(len(series)):
            val = series.iloc[i]

            if pd.isna(val):
                label = "missing"

            elif abs(z.iloc[i]) > 5:
                label = "strong_outlier"

            elif abs(z.iloc[i]) > 3:
                label = "mild_outlier"

            elif flat.iloc[i]:
                label = "flatline"

            elif jump.iloc[i]:
                label = "sudden_jump"

            elif iso_flag.iloc[i] == -1:
                label = "isolation_outlier"

            else:
                label = "normal"

            results.append({
                "Timestamp": df.iloc[i][timestamp_col] if timestamp_col in df.columns else i,
                "Tag": col,
                "Value": val,
                "Z_score": z.iloc[i],
                "Flatline": flat.iloc[i],
                "Jump": jump.iloc[i],
                "Isolation": iso_flag.iloc[i],
                "Status": label
            })

    return pd.DataFrame(results)


# -------------------------------
# Run
# -------------------------------
if __name__ == "__main__":

    file_path = "Multi_X_Multi_Y_Correct_Data.xlsx"

    if file_path.endswith(".csv"):
        df = pd.read_csv(file_path)
    else:
        df = pd.read_excel(file_path)

    df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")

    outliers_df = detect_outliers(df)

    outliers_df.to_excel("pure_data_outliers.xlsx", index=False)

    print("Outlier detection completed.")