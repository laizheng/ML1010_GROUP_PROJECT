# Modeling related
from sklearn import model_selection, preprocessing, linear_model, naive_bayes, metrics, svm
import pandas as pd, numpy as np
from keras.preprocessing import text, sequence
from keras.models import load_model
import pickle
import tensorflow as tf
from model_zoos import CnnWrapper, LstmWrapper
from sklearn.model_selection import StratifiedKFold
from keras.callbacks import EarlyStopping #, ModelCheckpoint
train_epochs = 200
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

def create_embedding_matrix(word_vector_file, tokenizer,
                            max_features,
                            embed_size):
    """
        :param max_features: how many unique words to use (i.e num rows in embedding vector).If None, use all
        :param embed_size: size of embedding vector
    """
    if max_features is None:
        max_features = float('inf')
    embeddings_index = {}
    for i, line in enumerate(open(word_vector_file)):
        values = line.split()
        if len(values[1:])<=2:
            continue
        embeddings_index[values[0]] = np.asarray(values[1:], dtype='float32')
    print("Total words in embedding file:" + str(len(embeddings_index)))
    word_index = tokenizer.word_index
    nb_words = min(max_features, len(word_index))
    # for words not in embedding file, init them to a random values
    all_embs = np.stack(embeddings_index.values())
    emb_mean, emb_std = all_embs.mean(), all_embs.std()
    embedding_matrix = np.random.normal(emb_mean, emb_std, (nb_words+1, embed_size))
    for word, i in sorted(word_index.items(), key=lambda kv: kv[1]):
        if i >= max_features:
            continue
        embedding_vector = embeddings_index.get(word)
        if embedding_vector is not None:
            embedding_matrix[i] = embedding_vector
    return embedding_matrix


def generate_word_sequence(texts, max_words, tokenizer):
    text_tokens = tokenizer.texts_to_sequences(texts)
    text_seqences = sequence.pad_sequences(text_tokens, maxlen=max_words)
    return text_seqences

def pr(x, y_i, y):
    p = x[y == y_i].sum(0)
    return (p + 1) / ((y == y_i).sum() + 1)

def main():
    np.random.seed(1234)
    # Parameters for feature extraction
    max_words = None # max number of words in a document to use
    max_features = None # num rows in embedding vector

    # read normailzed texts & labels, subsample to run on local machines
    df = pd.read_csv("../data/normalized_texts_labels.csv")
    df = df[["normalized_text", "fake"]]
    df.columns = ["texts", "labels"]

    # downsampling
    # df = df.iloc[list(range(0,df.shape[0],80))]

    print("# of NaN of text:" + str(df["texts"].isnull().sum()))
    print("# of NaN of label:" + str(df["labels"].isnull().sum()))
    df = df.dropna()
    print("dataset size:" + str(df.shape))
    # Encode labels
    label_encoder = preprocessing.LabelBinarizer()
    label_encoder.fit(df["labels"])
    labels_encoded = label_encoder.transform(df["labels"])
    y = df["labels"].values
    X = df["texts"].values

    # Convert texts to vector representation
    tokenizer = text.Tokenizer(num_words=max_features)
    tokenizer.fit_on_texts(df["texts"])
    # max_tokens_one_sent in wordvec and cnn
    text_tokens = tokenizer.texts_to_sequences(df["texts"])
    max_tokens_one_sent = 0
    min_tokens_one_sent = float('inf')
    for doc in text_tokens:
        max_tokens_one_sent = max(max_tokens_one_sent, len(doc))
        min_tokens_one_sent = min(min_tokens_one_sent, len(doc))
    print("Max # of tokens in docs: " + str(max_tokens_one_sent))
    print("Min # of tokens in docs: " + str(min_tokens_one_sent))

    if max_words is None:
        max_words = max_tokens_one_sent
    else:
        max_words = min(max_tokens_one_sent,max_words)

    if max_features is None:
        max_features = len(tokenizer.word_index)
    else:
        max_features = min(len(tokenizer.word_index),max_features)

    # Encoded sequence that represent a document
    embedding_matrix_fasttext = create_embedding_matrix('../wordvecs/wiki-news-300d-1M.vec',tokenizer, max_features, 300)

    # CNN model
    success = False
    print("CNN+FastText + LR+TFIDF_NB ensembled")
    while success is False:
        try:
            # cross validate
            skf = StratifiedKFold(n_splits=5, random_state=42)
            scores = {"train_acc": [], "val_acc": [], "train_auc": [], "val_auc": []}
            i = 0
            for train_index, test_index in skf.split(X, y):
                print("CV round %d..." % i)
                i += 1
                y_train, y_test = y[train_index], y[test_index]
                # LR + TFIDF_NB
                feature_extractor_tfv = TfidfVectorizer(min_df=3, max_features=None,
                                                        strip_accents='unicode', analyzer='word',
                                                        token_pattern=r'\w{1,}',
                                                        ngram_range=(1, 2), use_idf=1, smooth_idf=1, sublinear_tf=1,
                                                        stop_words='english')
                feature_extractor_tfv.fit(X[train_index])
                X_train_tfidf = feature_extractor_tfv.transform(X[train_index])
                X_test_tfidf = feature_extractor_tfv.transform(X[test_index])
                r = np.log(pr(X_train_tfidf, 1, y_train) / pr(X_train_tfidf, 0, y_train))
                X_train_tfidf_nb, X_test_tfidf_nb = X_train_tfidf.multiply(r), X_test_tfidf.multiply(r)
                lr_clf = LogisticRegression(C=4, dual=True)
                lr_clf.fit(X_train_tfidf_nb, y_train)
                print("lr_clf_score:" + str(lr_clf.score(X_test_tfidf_nb, y_test)))

                # CNN model
                cnn = CnnWrapper(embedding_matrix_fasttext, max_features, max_words)
                X_train_embedded = generate_word_sequence(X[train_index], max_words, tokenizer)
                X_test_embedded = generate_word_sequence(X[test_index], max_words,tokenizer)
                model = cnn.create_model()
                early = EarlyStopping(monitor="val_acc", mode="max", patience=5)
                callbacks_list = [early]
                model.fit(x=X_train_embedded, y=y_train, validation_data=(X_test_embedded, y_test),
                          epochs=train_epochs, callbacks=callbacks_list)

                train_pred_prob = model.predict(X_train_embedded)
                train_pred_prob += lr_clf.predict_proba(X_train_tfidf_nb)[:,1]
                train_pred_prob = train_pred_prob/2
                scores["train_acc"].append(metrics.accuracy_score(y_true=y_train, y_pred=(train_pred_prob > 0.5)))
                scores["train_auc"].append(metrics.roc_auc_score(y_train, train_pred_prob))
                val_pred_prob = model.predict(X_test_embedded)
                val_pred_prob += lr_clf.predict_proba(X_test_embedded)[:,1]
                val_pred_prob /= 2
                scores["val_acc"].append(metrics.accuracy_score(y_true=y_test, y_pred=(val_pred_prob > 0.5)))
                scores["val_auc"].append(metrics.roc_auc_score(y_test, val_pred_prob))
                print(scores)
            df_scores = pd.DataFrame(scores)
            df_scores.index.name = "CV round"
            df_scores = df_scores.T
            df_scores["mean"] = df_scores.mean(axis=1)
            df_scores["std"] = df_scores.std(axis=1)

            print(df_scores)
            # train on whole set
            #cnn_model = cnn.create_model()
            #early = EarlyStopping(monitor="acc", mode="max", patience=5)
            #callbacks_list = [early]
            #history = cnn_model.fit(x=X, y=labels_encoded,
            #                        epochs=train_epochs, callbacks=callbacks_list)
            success = True
        except tf.errors.ResourceExhaustedError as e:
            success = False
            print("Fail to acquire resources! Retrying.")
    #model_file_names = "../saved_models/cnn_fasttext.model"
    #print("saving models to " + model_file_names)
    #cnn_model.save(model_file_names)
    with open('../saved_models/cnn_fasttext_lr_tfidf_nb.model.cv.scores', 'wb') as file_pi:
        pickle.dump(df_scores, file_pi)
    #with open('../saved_models/cnn_fasttext.model.history', 'wb') as file_pi:
    #    pickle.dump(history.history, file_pi)

if __name__ == "__main__":
    main()
